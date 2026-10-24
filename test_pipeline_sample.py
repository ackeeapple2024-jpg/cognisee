"""
End-to-end pipeline test on saved sample data -- no live camera/mic/button
needed. Exercises the real routing (main.classify_question) plus the final
dual-engine pipeline (Qwen2-VL + EasyOCR) against the synthetic images in
samples/ (generate them first with `python samples/generate_samples.py`).

Covers all 5 real-world cases this project routes between, with medical
records and public transit -- the two priority use cases -- getting the
most coverage:
  1. medical records   -> ask_medical_record() (OCR + DB-sync stub)
  2. public transit     -> ask_ocr() (signage/schedule reading)
  3. color              -> ask_vlm()
  4. object ID          -> ask_vlm()
  5. scene description / presence -> ask_vlm()

Usage:
    python test_pipeline_sample.py
"""

import os
import time

import cv2

import config
import main as cognisee

SAMPLES_DIR = config.SAMPLES_DIR

# (image filename, question, expected route "medical"|"ocr"|"vlm")
CASES = [
    # -- public transit (priority case #2) --
    ("korean_subway_sign.png", "이 표지판에 뭐라고 써 있어?", "ocr"),
    ("korean_subway_sign.png", "몇 번 출구야?", "ocr"),
    ("english_exit_sign.png", "which exit is this?", "ocr"),
    ("english_exit_sign.png", "read the sign", "ocr"),
    ("bus_schedule_sign_en.png", "what bus is this?", "ocr"),
    ("bus_schedule_sign_en.png", "when does the bus arrive?", "ocr"),
    ("bus_schedule_sign_en.png", "which gate is it at?", "ocr"),

    # -- medical records (priority case #1) --
    ("prescription_label_en.png", "what medication is this?", "medical"),
    ("prescription_label_en.png", "what's the dosage?", "medical"),
    ("prescription_label_ko.png", "무슨 약이야?", "medical"),
    ("prescription_label_ko.png", "복용법이 뭐야?", "medical"),

    # -- color / object ID / scene / presence (VLM route) --
    ("red_mug_on_table.png", "what color is the mug?", "vlm"),
    ("red_mug_on_table.png", "is there a cup on the table?", "vlm"),
    ("desk_scene.png", "describe what you see on the desk", "vlm"),
    ("empty_hallway.png", "is anyone in front of me?", "vlm"),
    ("person_in_hallway.png", "is anyone in front of me?", "vlm"),
]


def _load(filename: str):
    path = os.path.join(SAMPLES_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- run `python samples/generate_samples.py` first."
        )
    frame = cv2.imread(path)
    if frame is None:
        raise RuntimeError(f"failed to read {path}")
    return frame


def main() -> None:
    failures = 0
    for filename, question, expected_route in CASES:
        frame = _load(filename)
        actual_route = cognisee.classify_question(question)
        route_ok = actual_route == expected_route
        if not route_ok:
            failures += 1
        print(f"\n[{filename}] Q: {question!r}")
        print(f"  routed to: {actual_route}  (expected {expected_route})  {'OK' if route_ok else 'MISMATCH'}")

        start = time.time()
        if actual_route == "medical":
            answer = cognisee.ask_medical_record(frame, question)
        elif actual_route == "ocr":
            answer = cognisee.ask_ocr(frame, question)
        else:
            answer = cognisee.ask_vlm(frame, question)
        elapsed = time.time() - start
        print(f"  answer ({elapsed:.2f}s): {answer!r}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} routed as expected.")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
