"""
Benchmarks the 5 question-task-types CogniSee routes between, in both
English and Korean, on saved sample images -- no live camera/mic/button
needed. Run this before plugging in external hardware to find, on THIS
machine, which VLM backend is both accurate and fast enough (< 3s).

The 5 task types (see README.md "dual pipeline" section):
  1. reading        -> OCR route (EasyOCR, not a VLM choice)
  2. color           -> VLM route
  3. object_id        -> VLM route
  4. scene_description -> VLM route
  5. presence          -> VLM route

For each VLM task type, every backend in VLM_BACKENDS_TO_TEST is tried in
turn, in both languages, and timed. Reading is tested once (OCR has no
backend choice here) in both languages.

Usage:
    python benchmark_task_types.py                    # full run, all backends
    python benchmark_task_types.py --backends smolvlm  # just one backend (faster iteration)
    python benchmark_task_types.py --skip-slow         # skip qwen2vl/moondream (in case they're too slow to bother waiting on)
"""

import argparse
import csv
import json
import os
import time
import traceback

import cv2

import config
import main as cognisee

SAMPLES_DIR = config.SAMPLES_DIR
VLM_BACKENDS_ALL = ["smolvlm", "qwen2vl", "qwen25vl", "moondream"]

# (task_type, image_filename, language, question, expected_keywords)
# expected_keywords: any one of these (case-insensitive substring) appearing
# in the answer counts as "looks correct" for the auto-grade column. This is
# a rough automatic signal, not a substitute for reading the actual answers
# printed below -- some correct answers won't contain the exact keyword.
CASES = [
    ("reading", "english_exit_sign.png", "en", "What does this sign say?", ["exit", "5"]),
    ("reading", "korean_subway_sign.png", "ko", "이 표지판에 뭐라고 써 있어?", ["2호선", "출구", "시청"]),

    ("color", "red_mug_on_table.png", "en", "What color is the mug?", ["red"]),
    ("color", "red_mug_on_table.png", "ko", "머그컵이 무슨 색이야?", ["빨강", "빨간", "red"]),

    ("object_id", "red_mug_on_table.png", "en", "What object is on the table?", ["mug", "cup"]),
    ("object_id", "red_mug_on_table.png", "ko", "탁자 위에 뭐가 있어?", ["머그", "컵"]),

    ("scene_description", "desk_scene.png", "en", "Describe what you see on the desk.", ["laptop", "mug", "cup", "book"]),
    ("scene_description", "desk_scene.png", "ko", "책상 위에 뭐가 보이는지 설명해줘.", ["노트북", "머그", "컵", "책"]),

    ("presence", "empty_hallway.png", "en", "Is there anyone in front of me?", ["no", "nobody", "not", "empty", "no one"]),
    ("presence", "person_in_hallway.png", "en", "Is there anyone in front of me?", ["yes", "person", "someone"]),
    ("presence", "empty_hallway.png", "ko", "제 앞에 사람이 있어?", ["없", "아니"]),
    ("presence", "person_in_hallway.png", "ko", "제 앞에 사람이 있어?", ["있", "사람"]),
]


def _load(filename: str):
    path = os.path.join(SAMPLES_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found -- run `python samples/generate_samples.py` first.")
    frame = cv2.imread(path)
    if frame is None:
        raise RuntimeError(f"failed to read {path}")
    return frame


def _looks_correct(answer: str, expected_keywords: list) -> bool:
    a = answer.lower()
    return any(kw.lower() in a for kw in expected_keywords)


def run(backends_to_test: list, results_path: str) -> list:
    results = []

    # -- reading (OCR, no backend choice) --
    for task_type, filename, lang, question, keywords in CASES:
        if task_type != "reading":
            continue
        frame = _load(filename)
        print(f"\n[reading/{lang}] {filename}  Q: {question!r}")
        start = time.time()
        try:
            answer = cognisee.ask_ocr(frame, question)
            elapsed = time.time() - start
            ok = _looks_correct(answer, keywords)
            print(f"  ({elapsed:.2f}s) A: {answer!r}  {'[looks OK]' if ok else '[check manually]'}")
            results.append(dict(task_type=task_type, engine="easyocr", language=lang,
                                 image=filename, question=question, answer=answer,
                                 elapsed_sec=round(elapsed, 2), under_3s=elapsed < 3.0,
                                 auto_graded_ok=ok, error=None))
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append(dict(task_type=task_type, engine="easyocr", language=lang,
                                 image=filename, question=question, answer=None,
                                 elapsed_sec=None, under_3s=False, auto_graded_ok=False,
                                 error=str(e)))

    # -- the 4 VLM task types, once per backend --
    for backend in backends_to_test:
        print(f"\n{'=' * 70}\nBACKEND: {backend}\n{'=' * 70}")
        config.VLM_BACKEND = backend
        cognisee._vlm_handle = None  # force reload under this backend

        try:
            load_start = time.time()
            cognisee._get_vlm()
            load_elapsed = time.time() - load_start
            print(f"(model load: {load_elapsed:.1f}s, one-time cost not counted in per-question timing)")
        except Exception as e:
            print(f"COULD NOT LOAD {backend}: {e}")
            traceback.print_exc()
            for task_type, filename, lang, question, keywords in CASES:
                if task_type == "reading":
                    continue
                results.append(dict(task_type=task_type, engine=backend, language=lang,
                                     image=filename, question=question, answer=None,
                                     elapsed_sec=None, under_3s=False, auto_graded_ok=False,
                                     error=f"load failed: {e}"))
            continue

        for task_type, filename, lang, question, keywords in CASES:
            if task_type == "reading":
                continue
            frame = _load(filename)
            print(f"\n[{task_type}/{lang}] {filename}  Q: {question!r}")
            start = time.time()
            try:
                answer = cognisee.ask_vlm(frame, question)
                elapsed = time.time() - start
                ok = _looks_correct(answer, keywords)
                under_3s = elapsed < 3.0
                print(f"  ({elapsed:.2f}s{'  UNDER 3s' if under_3s else '  OVER 3s'}) A: {answer!r}  "
                      f"{'[looks OK]' if ok else '[check manually]'}")
                results.append(dict(task_type=task_type, engine=backend, language=lang,
                                     image=filename, question=question, answer=answer,
                                     elapsed_sec=round(elapsed, 2), under_3s=under_3s,
                                     auto_graded_ok=ok, error=None))
            except Exception as e:
                print(f"  FAILED: {e}")
                results.append(dict(task_type=task_type, engine=backend, language=lang,
                                     image=filename, question=question, answer=None,
                                     elapsed_sec=None, under_3s=False, auto_graded_ok=False,
                                     error=str(e)))

    with open(results_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    with open(results_path.replace(".csv", ".json"), "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nSaved results to {results_path} and {results_path.replace('.csv', '.json')}")
    return results


def print_summary(results: list) -> None:
    print(f"\n{'=' * 70}\nSUMMARY: avg latency + accuracy by engine\n{'=' * 70}")
    by_engine: dict = {}
    for r in results:
        by_engine.setdefault(r["engine"], []).append(r)

    for engine, rows in by_engine.items():
        timed = [r for r in rows if r["elapsed_sec"] is not None]
        if not timed:
            print(f"  {engine}: all failed to run")
            continue
        avg = sum(r["elapsed_sec"] for r in timed) / len(timed)
        under3 = sum(1 for r in timed if r["under_3s"])
        ok = sum(1 for r in timed if r["auto_graded_ok"])
        print(f"  {engine}: avg={avg:.2f}s  under_3s={under3}/{len(timed)}  "
              f"auto_graded_ok={ok}/{len(timed)}  failed={len(rows) - len(timed)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backends", nargs="+", choices=VLM_BACKENDS_ALL, default=VLM_BACKENDS_ALL)
    parser.add_argument("--skip-slow", action="store_true", help="skip qwen2vl and moondream, smolvlm only")
    parser.add_argument("--out", default="benchmark_results.csv")
    args = parser.parse_args()

    backends = ["smolvlm"] if args.skip_slow else args.backends

    results = run(backends, args.out)
    print_summary(results)


if __name__ == "__main__":
    main()
