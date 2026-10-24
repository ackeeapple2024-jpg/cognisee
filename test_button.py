"""
Trigger button (Bluetooth clicker) test/diagnostic script.

CONFIRMED on this machine (2026-09-19): the clicker sends a real macOS
"volume up" system-defined media-key event (NX_KEYTYPE_SOUND_UP, keycode 0)
on every click -- the same volume-hijack behavior it had on Windows, just
observable here instead of hidden. config.BUTTON_BACKEND is set to
"media_key_tap", which intercepts and SUPPRESSES that event via a low-level
CGEventTap (main.MediaKeyTapButtonListener) so the system volume never
actually changes, and fires on_press() instead.

Usage:
    python test_button.py
        Confirms the configured backend (media_key_tap by default) actually
        detects real clicks, and that the system volume genuinely does NOT
        change anymore. Watch the menu bar volume icon while clicking --
        if it still visibly moves, the tap isn't suppressing correctly
        (check for the Input Monitoring permission prompt/log message).

    python test_button.py --probe-media-keys
        Logs every system-defined media key code seen (WITHOUT suppressing
        any of them) -- use this if you get a different clicker/remote in
        the future and need to find its key code instead of assuming 0.

    python test_button.py --probe
        Legacy check: logs every *normal* key event pynput sees. Useful
        only to confirm the negative result (that this clicker's presses
        are invisible to a plain keyboard hook) -- expect to see nothing
        here when you click it.

    python test_button.py --watch-volume
        Polls system volume and reports changes after the fact. Superseded
        by media_key_tap for the real backend, kept for comparison/debugging.
"""

import argparse
import time

import config
from main import get_button_listener, CoreAudioVolumeWatchListener, MediaKeyTapButtonListener


def cmd_probe() -> None:
    from pynput import keyboard

    print("Listening for ALL normal key events system-wide. Click the clicker a few times.")
    print("Expected result: nothing prints, because this clicker's presses are system-defined")
    print("media-key events, not normal keyDown events pynput can see.")
    print("Press Ctrl+C here to stop.\n")

    def on_press(key):
        print(f"key event: {key!r}")

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def cmd_probe_media_keys() -> None:
    print("Listening for ALL system-defined media-key events (volume/brightness/play/etc),")
    print("WITHOUT suppressing any of them -- click the clicker, or any other media key,")
    print("and note the key code that appears. Add it to config.BUTTON_MEDIA_KEY_CODES if")
    print("it's not already there. Press Ctrl+C to stop.\n")
    print("(First run may prompt for Input Monitoring permission -- grant it to Terminal")
    print(" and re-run if nothing appears when you click.)\n")

    def on_press():
        pass  # logging happens inside the listener itself; see below

    listener = MediaKeyTapButtonListener(on_press, trigger_key_codes=set())  # empty set = never suppress, just observe via the log line below
    # monkeypatch: log every key code seen, not just ones in trigger_key_codes
    from AppKit import NSEvent

    def logging_callback(proxy, event_type, event, refcon):
        if event_type == listener.NX_SYSDEFINED:
            try:
                ns_event = NSEvent.eventWithCGEvent_(event)
                if ns_event is not None and ns_event.subtype() == listener.NX_SUBTYPE_AUX_CONTROL_BUTTONS:
                    data1 = ns_event.data1()
                    key_code = (data1 & 0xFFFF0000) >> 16
                    key_flags = data1 & 0x0000FFFF
                    key_is_pressed = ((key_flags & 0xFF00) >> 8) == 0x0A
                    if key_is_pressed:
                        print(f"media key code: {key_code}")
            except Exception as e:
                print(f"(error decoding event: {e})")
        return event  # never suppress in this diagnostic mode

    listener._tap_callback = logging_callback
    listener.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def cmd_watch_volume() -> None:
    print("Polling system output volume. Click the clicker and watch for changes.")
    print("Press Ctrl+C to stop.\n")

    def on_change():
        print("^ volume-watch fired (see log line above)")

    listener = CoreAudioVolumeWatchListener(on_change, poll_interval=0.1)
    listener.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def cmd_live() -> None:
    print(f"Using configured BUTTON_BACKEND={config.BUTTON_BACKEND!r}. Click the clicker; "
          f"each press should print a line below, and the system volume should NOT change.")
    print("Watch the menu bar volume icon while clicking, to confirm suppression is working.")
    print("Press Ctrl+C to stop.\n")
    count = 0

    def on_press():
        nonlocal count
        count += 1
        print(f"button press #{count} detected")

    listener = get_button_listener(on_press)
    listener.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="log every NORMAL key event pynput sees (expect nothing for this clicker)")
    parser.add_argument("--probe-media-keys", action="store_true", help="log every system-defined media-key code seen, without suppressing any")
    parser.add_argument("--watch-volume", action="store_true", help="poll system volume and report changes after the fact (superseded by media_key_tap)")
    args = parser.parse_args()

    if args.probe:
        cmd_probe()
    elif args.probe_media_keys:
        cmd_probe_media_keys()
    elif args.watch_volume:
        cmd_watch_volume()
    else:
        cmd_live()


if __name__ == "__main__":
    main()
