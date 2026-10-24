"""
Microphone identification/test script. Run this BEFORE trusting
config.MIC_DEVICE_INDEX.

On the Windows laptop, the correct device ("AB13X USB Audio") had a silent
phantom duplicate that could only be told apart by actually recording and
comparing signal levels -- device names alone were not enough. Re-verify
the same way here; do not assume macOS won't also expose a duplicate/virtual
device with a similar name.

Usage:
    python test_mic.py --list                  # list all input devices
    python test_mic.py --probe                 # record 3s from every input device, report peak level
    python test_mic.py --record <index> [sec]   # record from one device, save a WAV to listen to
"""

import argparse
import sys

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile

import config


def cmd_list() -> None:
    devices = sd.query_devices()
    print("Input-capable devices:")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            default_marker = " (default)" if idx == sd.default.device[0] else ""
            print(f"  index={idx}  name={dev['name']!r}  channels={dev['max_input_channels']}  "
                  f"default_samplerate={dev['default_samplerate']}{default_marker}")


def _record(index: int, seconds: float) -> np.ndarray:
    audio = sd.rec(
        int(seconds * config.SAMPLE_RATE),
        samplerate=config.SAMPLE_RATE,
        channels=config.CHANNELS,
        dtype=config.RECORDING_DTYPE,
        device=index,
    )
    sd.wait()
    return audio


def cmd_probe(seconds: float) -> None:
    devices = sd.query_devices()
    print(f"Recording {seconds:.1f}s from each input device -- make some noise / talk during this.")
    results = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] <= 0:
            continue
        print(f"\n-- index={idx} name={dev['name']!r} -- recording now...")
        try:
            audio = _record(idx, seconds)
        except Exception as e:
            print(f"   failed to record: {e}")
            continue
        peak = int(np.abs(audio.astype(np.int64)).max()) if audio.size else 0
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
        results.append((idx, dev["name"], peak, rms))
        print(f"   peak={peak}  rms={rms:.1f}")

    print("\nSummary (higher peak/rms while you were talking = likely the real mic):")
    for idx, name, peak, rms in sorted(results, key=lambda r: -r[2]):
        print(f"  index={idx}  name={name!r}  peak={peak}  rms={rms:.1f}")


def cmd_record(index: int, seconds: float) -> None:
    print(f"Recording {seconds:.1f}s from index {index}...")
    audio = _record(index, seconds)
    out_path = f"mic_index_{index}_sample.wav"
    wavfile.write(out_path, config.SAMPLE_RATE, audio)
    print(f"Saved to {out_path}. Play it back to confirm it's the right mic and levels look right.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--probe", action="store_true", help="record briefly from every input device and compare levels")
    parser.add_argument("--probe-seconds", type=float, default=3.0)
    parser.add_argument("--record", type=int, metavar="INDEX", help="record from one device index and save a WAV")
    parser.add_argument("--record-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.probe:
        cmd_probe(args.probe_seconds)
    elif args.record is not None:
        cmd_record(args.record, args.record_seconds)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
