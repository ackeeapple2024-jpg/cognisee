"""Pre-flight check: is this Mac ready to run CogniSee?

    ./venv/bin/python doctor.py           # full check, incl. "press the clicker"
    ./venv/bin/python doctor.py --quick   # skip the interactive steps

Checks, in order: platform, no other CogniSee running, models downloaded,
camera found + delivering frames, mic found + delivering real audio,
audio output (plays a tone and a phrase), clicker (tap permission, then
waits for a real press). Each failure prints what to fix. Exit code is
nonzero if anything failed.
"""

import argparse
import os
import platform
import sys
import threading
import time

import numpy as np

import config

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, fix: str = "") -> bool:
    results.append((ok, label))
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    if not ok and fix:
        print(f"        -> {fix}")
    return ok


def check_platform() -> None:
    print("Platform")
    check(platform.system() == "Darwin" and platform.machine() == "arm64",
          f"macOS on Apple Silicon ({platform.system()} {platform.machine()})",
          "CogniSee only supports Apple Silicon Macs.")


def check_not_running() -> None:
    import fcntl
    path = os.path.join(config.BASE_DIR, ".cognisee.lock")
    if not os.path.exists(path):
        check(True, "no other CogniSee pipeline running")
        return
    with open(path) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            check(True, "no other CogniSee pipeline running")
        except OSError:
            pid = f.read().strip()
            check(False, f"another CogniSee pipeline is running (pid {pid})",
                  f"It holds the camera, mic and clicker. Stop it: kill {pid}")


def check_models() -> None:
    print("Models")
    from download_models import VLM_MODEL_IDS
    attr = VLM_MODEL_IDS.get(config.VLM_BACKEND)
    if attr:
        from huggingface_hub import snapshot_download
        model_id = getattr(config, attr)
        try:
            snapshot_download(model_id, local_files_only=True)
            check(True, f"VLM {model_id}")
        except Exception:
            check(False, f"VLM {model_id} not downloaded", "Run: ./venv/bin/python download_models.py")
    try:
        from faster_whisper.utils import download_model
        download_model(config.WHISPER_MODEL_SIZE, local_files_only=True)
        check(True, f"Whisper '{config.WHISPER_MODEL_SIZE}'")
    except Exception:
        check(False, f"Whisper '{config.WHISPER_MODEL_SIZE}' not downloaded", "Run: ./venv/bin/python download_models.py")
    try:
        import easyocr
        easyocr.Reader(config.OCR_LANGUAGES, gpu=False, download_enabled=False, verbose=False)
        check(True, f"EasyOCR {config.OCR_LANGUAGES}")
    except Exception:
        check(False, f"EasyOCR {config.OCR_LANGUAGES} not downloaded", "Run: ./venv/bin/python download_models.py")


def check_camera() -> None:
    import cv2
    import main
    print("Camera")
    try:
        idx = main.resolve_camera_index()
    except main.CameraNotFoundError:
        check(False, f"no camera with native {config.CAMERA_NATIVE_WIDTH}x{config.CAMERA_NATIVE_HEIGHT} found",
              "Plug the USB camera directly into the Mac (not a shared hub). If it IS plugged in, "
              "Terminal may lack Camera permission: System Settings -> Privacy & Security -> Camera.")
        return
    check(True, f"camera found at index {idx} ({config.CAMERA_NATIVE_WIDTH}x{config.CAMERA_NATIVE_HEIGHT})")
    try:
        frame = main.capture_frame()
        sharp = cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        path = os.path.join(config.BASE_DIR, "doctor_camera_frame.jpg")
        cv2.imwrite(path, frame)
        check(True, f"frame captured (sharpness {sharp:.0f}), saved to {os.path.basename(path)} -- open it to check the view")
    except Exception as e:
        check(False, f"camera found but frame capture failed: {e}", "Unplug and replug the camera, then re-run.")
    finally:
        main.release_camera()


def check_mic() -> None:
    import sounddevice as sd
    import main
    print("Microphone")
    idx = main.resolve_mic_device_index()
    if idx is None:
        check(False, f"no input device matching MIC_NAME_HINT={config.MIC_NAME_HINT!r}",
              "Connect your Bluetooth headset (put them in your ears so they're active), or set "
              "MIC_NAME_HINT in config.py to part of your mic's name. Inputs seen: "
              + ", ".join(d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0))
        return
    check(True, f"mic found: {sd.query_devices(idx)['name']}")
    chunks = []
    stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=config.CHANNELS,
                            dtype=config.RECORDING_DTYPE, device=idx,
                            callback=lambda d, f, t, s: chunks.append(d.copy()))
    stream.start()
    t0 = time.time()
    live_at = None
    while time.time() - t0 < config.MIC_LIVE_WAIT_SEC + 2:
        if live_at is None and any(np.any(c) for c in chunks[-20:]):
            live_at = time.time() - t0
            break
        time.sleep(0.05)
    stream.stop()
    stream.close()
    check(live_at is not None,
          f"mic delivering real audio (live after {live_at:.1f}s)" if live_at is not None else "mic opened but only silence",
          "Terminal may lack Microphone permission: System Settings -> Privacy & Security -> Microphone.")


def check_output() -> None:
    import main
    print("Audio output")
    try:
        main.play_start_tone()
        main.speak("CogniSee doctor test.")
        check(True, "played a beep and 'CogniSee doctor test' -- confirm you heard both")
    except Exception as e:
        check(False, f"audio output failed: {e}", "Check the output device in System Settings -> Sound.")


def check_button(interactive: bool) -> None:
    import Quartz
    import main
    print("Clicker")
    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                  Quartz.kCGEventTapOptionListenOnly,
                                  Quartz.CGEventMaskBit(main.MediaKeyTapButtonListener.NX_SYSDEFINED),
                                  lambda *a: a[2], None)
    if not check(tap is not None, "permission to read the clicker",
                 "System Settings -> Privacy & Security -> Input Monitoring AND Accessibility: "
                 "enable Terminal (or whatever app runs CogniSee), then fully quit and reopen it."):
        return
    if not interactive:
        return
    pressed = threading.Event()
    listener = main.get_button_listener(pressed.set)
    listener.start()
    print("        Press the clicker button now (15s)...", flush=True)
    got = pressed.wait(15)
    listener.stop()
    check(got, "clicker press received",
          "Pair the clicker in System Settings -> Bluetooth and make sure it's on. "
          "If it's a different model, run test_button.py to see what it sends.")


def main_() -> None:
    parser = argparse.ArgumentParser(description="CogniSee pre-flight check")
    parser.add_argument("--quick", action="store_true", help="skip interactive steps (clicker press)")
    args = parser.parse_args()

    check_platform()
    check_not_running()
    check_models()
    check_camera()
    check_mic()
    check_output()
    check_button(interactive=not args.quick)

    failed = [label for ok, label in results if not ok]
    print()
    if failed:
        print(f"{len(failed)} problem(s) -- fix the FAIL lines above, then re-run doctor.py.")
        sys.exit(1)
    print("All checks passed. Start CogniSee with ./run.sh")


if __name__ == "__main__":
    main_()
