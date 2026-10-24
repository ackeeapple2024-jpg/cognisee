"""
TTS output test. Confirms audio output device + the configured TTS backend
(config.TTS_BACKEND: "say" or "pyttsx3") both work before wiring in the
full pipeline.

Usage:
    python test_tts.py                       # speak a default test phrase
    python test_tts.py "custom phrase"        # speak a custom phrase
    python test_tts.py --list-voices          # (macOS "say" backend) list available system voices
"""

import argparse
import subprocess
import sys

import config
from main import speak, IS_MACOS

DEFAULT_PHRASE = "CogniSee text to speech test. If you can hear this clearly, audio output is working."


def cmd_list_voices() -> None:
    if not IS_MACOS:
        print("--list-voices only supported on macOS (uses the `say` command).")
        return
    subprocess.run(["say", "-v", "?"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phrase", nargs="?", default=DEFAULT_PHRASE)
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    if args.list_voices:
        cmd_list_voices()
        return

    print(f"TTS_BACKEND={config.TTS_BACKEND!r}  TTS_VOICE={config.TTS_VOICE!r}  TTS_RATE={config.TTS_RATE}")
    print("Set your system output device (headphone jack / earphones) as the default output before running this.")
    print(f"Speaking: {args.phrase!r}")
    speak(args.phrase)
    print("Done. If you didn't hear anything, check System Settings -> Sound -> Output.")


if __name__ == "__main__":
    main()
