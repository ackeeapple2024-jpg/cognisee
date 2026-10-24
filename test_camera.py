"""
Camera identification/test script. Run this BEFORE trusting config.CAMERA_INDEX.

On macOS, OpenCV talks to AVFoundation, not DirectShow -- indices and any
reported names will likely not match whatever was true on the Windows
laptop. Do not assume index 0 or 1 is the external USB webcam; check.

Usage:
    python test_camera.py --list              # probe indices 0-7, report which open
    python test_camera.py --preview <index>    # open one index, save a frame to look at
    python test_camera.py --preview <index> --live   # live OpenCV preview window
"""

import argparse
import sys
import time

import cv2

import config
from main import list_cameras


def cmd_list(max_index: int) -> None:
    found = list_cameras(max_index)
    if not found:
        print(f"No cameras found in indices 0..{max_index - 1}.")
        return
    print(f"Found {len(found)} camera(s):")
    for info in found:
        shape = info.get("shape")
        print(f"  index={info['index']}  frame_ok={info['frame_ok']}  shape={shape}")
    print(
        "\nNote: macOS/OpenCV does not reliably expose device names here. "
        "To tell devices apart, use --preview <index> on each and look at "
        "(or check the resolution/aspect ratio of) the captured frame -- "
        "the built-in camera and the external USB webcam should look "
        "obviously different."
    )


def cmd_preview(index: int, live: bool) -> None:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"Could not open camera index {index}")
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)

    if live:
        # This specific webcam+USB-C-dongle combo has been observed to drop
        # the stream after a few seconds of continuous reads (likely a
        # power/bandwidth limit of the dongle, not a software bug) --
        # reopen on a failed read instead of just quitting, so a transient
        # dropout doesn't kill the whole preview window.
        print("Press 'q' in the preview window to quit.")
        consecutive_failures = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                consecutive_failures += 1
                print(f"frame read failed (attempt {consecutive_failures}), reopening...")
                cap.release()
                time.sleep(0.3)
                cap = cv2.VideoCapture(index)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
                if consecutive_failures >= 20:
                    print("too many consecutive failures, giving up")
                    break
                continue
            consecutive_failures = 0
            cv2.imshow(f"camera index {index}", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        cap.release()
        cv2.destroyAllWindows()
    else:
        # discard warmup frames like the real pipeline does
        frame = None
        for _ in range(config.CAMERA_WARMUP_FRAMES + 1):
            ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            print("frame read failed")
            sys.exit(1)
        out_path = f"camera_index_{index}_sample.jpg"
        cv2.imwrite(out_path, frame)
        print(f"Saved a frame from index {index} to {out_path} ({frame.shape}). Open it and check which camera this is.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="probe camera indices and report which open")
    parser.add_argument("--max-index", type=int, default=8)
    parser.add_argument("--preview", type=int, metavar="INDEX", help="capture (or live-preview) a frame from this index")
    parser.add_argument("--live", action="store_true", help="with --preview, show a live OpenCV window instead of saving one frame")
    args = parser.parse_args()

    if args.list:
        cmd_list(args.max_index)
    elif args.preview is not None:
        cmd_preview(args.preview, args.live)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
