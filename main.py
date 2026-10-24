"""
CogniSee -- core pipeline.

Button press (press-to-talk) -> record audio + grab a camera frame ->
transcribe the question (Whisper) -> route to OCR or a VLM -> speak the
answer (TTS) -> repeat. Fully local/offline, no wake word, no always-on
listening.

Reconstructed on this Mac (2026-09-19) from a working Windows
implementation that is not present on this machine. The pipeline shape,
class names, and dual-OCR/VLM routing design below match what was described
of that implementation; exact internals (esp. STT/TTS library choice) are
this rebuild's judgment calls, not a verified port -- see config.py comments
for what's flagged VERIFY vs. what's a fresh design decision.

Platform-specific pieces already rewritten for macOS per the porting notes:
  - play_tone(): sounddevice+numpy sine wave, replaces winsound.Beep
  - list_cameras()/resolve_camera_index(): no cv2.CAP_DSHOW / pygrabber,
    OpenCV uses AVFoundation automatically on macOS
  - button capture: pynput global hotkey listener by default, with a
    Core-Audio-volume-watch backend present but NOT assumed necessary --
    test_button.py decides which one this specific clicker needs
"""

from __future__ import annotations

import os

# Must be set before transformers/tokenizers is imported anywhere (directly
# or via faster_whisper/huggingface_hub). Without this, a background
# tokenizer fork warning ("the current process just got forked...") was
# observed repeatedly on real hardware, and forking after PortAudio/
# CoreAudio streams have been touched is a plausible cause of the
# intermittent mic-open PortAudioError [-9986] also seen on real hardware
# (see MIC_OPEN_RETRIES in config.py) -- CoreAudio's Mach-port-based state
# does not survive fork() cleanly on macOS. Disabling tokenizer
# parallelism removes the fork instead of just retrying around its effects.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import dataclasses
import logging
import platform
import queue
import subprocess
import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np
import sounddevice as sd

import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cognisee")

IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Tones (cross-platform replacement for winsound.Beep)
# ---------------------------------------------------------------------------

def _tone_wave(freq_hz: float, duration_sec: float, volume: float, fade_sec: float = 0.008) -> np.ndarray:
    t = np.linspace(0, duration_sec, int(config.TONE_SAMPLE_RATE * duration_sec), endpoint=False)
    wave = volume * np.sin(2 * np.pi * freq_hz * t)
    # short fade in/out at the edges to avoid clicks, not to add dead air
    fade_len = max(1, int(fade_sec * config.TONE_SAMPLE_RATE))
    fade = np.linspace(0, 1, fade_len)
    wave[:fade_len] *= fade
    wave[-fade_len:] *= fade[::-1]
    return wave


def _reset_portaudio() -> None:
    """Force PortAudio to rescan the system's audio devices.

    CONFIRMED on real hardware (2026-09-19): after many device
    reconnect/reorder events in one long-running process (Bluetooth
    Bluetooth headset, a flaky USB-C dongle carrying the mic+camera), audio stream
    opens started throwing PortAudioError [-9986] / CoreAudio -10851
    across UNRELATED devices simultaneously (Bluetooth headset output AND the
    dongle's mic) even though every device was confirmed fine at the OS
    level (system_profiler, ioreg) at the same moment -- meaning
    PortAudio's own internal device table had gone stale relative to
    what's actually connected, not that anything was really wrong with
    the hardware. Re-initializing PortAudio forces a fresh device scan.
    """
    try:
        sd._terminate()
        sd._initialize()
        log.info("PortAudio device table refreshed")
    except Exception:
        log.exception("failed to refresh PortAudio device table")


def play_tone(freq_hz: float, duration_sec: float = config.TONE_DURATION_SEC,
              volume: float = config.TONE_VOLUME) -> None:
    """Play a short sine-wave beep. Blocks until playback finishes.

    Deliberately swallows playback errors (logs instead of raising): this
    is a UI confirmation cue, and a failure here (e.g. an output device
    sample-rate mismatch -- seen for real with Bluetooth headset over Bluetooth,
    see TONE_SAMPLE_RATE's comment in config.py) must never be allowed to
    abort whatever recording logic the caller runs after it. Retries once
    after a PortAudio device-table refresh (see _reset_portaudio()) before
    giving up silently.
    """
    wave = _tone_wave(freq_hz, duration_sec, volume)
    if config.TONE_LEADING_SILENCE_SEC > 0:
        wave = np.concatenate([np.zeros(int(config.TONE_LEADING_SILENCE_SEC * config.TONE_SAMPLE_RATE)), wave])
    for attempt in range(2):
        try:
            sd.play(wave.astype(np.float32), samplerate=config.TONE_SAMPLE_RATE)
            sd.wait()
            return
        except Exception:
            if attempt == 0:
                log.warning("tone playback failed, refreshing PortAudio and retrying once")
                _reset_portaudio()
            else:
                log.exception("tone playback failed again (continuing without it)")


def play_start_tone() -> None:
    """'Mic activated' cue -- single beep."""
    play_tone(config.TONE_START_FREQ_HZ)


def play_stop_tone() -> None:
    """'Mic deactivated' cue -- single beep."""
    play_tone(config.TONE_STOP_FREQ_HZ)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def list_cameras(max_index: int = 8) -> list[dict]:
    """Probe camera indices 0..max_index-1, return which ones open and their
    reported frame size. macOS/OpenCV uses AVFoundation automatically -- no
    backend flag needed, unlike the Windows cv2.CAP_DSHOW + pygrabber path.
    """
    found = []
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        info = {"index": idx, "opened": True, "frame_ok": ok}
        if ok and frame is not None:
            info["shape"] = frame.shape
        cap.release()
        found.append(info)
    return found


class CameraNotFoundError(RuntimeError):
    """The external camera couldn't be found by its resolution fingerprint.
    Deliberately distinct from a generic RuntimeError so callers can tell
    "camera is gone" apart from other capture failures if they need to."""


def resolve_camera_index(max_index: int = 8) -> int:
    """Find the external camera by its native resolution, NOT by a fixed
    index number.

    CONFIRMED on this machine (2026-09-19) that cv2 index numbers are
    unstable: this Mac also has 2 iPhones available as Continuity
    Camera sources, which join/leave the AVFoundation device list based on
    proximity/wake state and shift every other camera's index around
    between runs -- and separately confirmed that OpenCV's AVFoundation
    backend does NOT use the same enumeration order as querying
    AVFoundation directly (PyObjC's AVCaptureDevice.devicesWithMediaType_),
    so there is no reliable way to translate "AVFoundation says device X is
    at position N" into "cv2 index N is device X" either. Position-based
    identification of any kind is a dead end here.

    What IS stable: querying each AVCaptureDevice's .formats() shows the
    configured external camera's native resolution (config.CAMERA_NATIVE_*
    -- see config.py for which physical camera this currently points at
    and why) doesn't collide with the built-in FaceTime HD camera or
    either iPhone's Continuity Camera, both of which report 1920x1080 and
    not the external camera's native resolution. So instead of trusting an
    index, open each index, check its native (unconstrained) resolution,
    and return whichever one actually matches.
    """
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            if w == config.CAMERA_NATIVE_WIDTH and h == config.CAMERA_NATIVE_HEIGHT:
                log.info("resolved external camera at index %d (matched native %dx%d)", idx, w, h)
                return idx
    # CONFIRMED on real hardware (2026-09-19): silently falling back to
    # config.CAMERA_INDEX here (typically 0) meant that when the external
    # webcam dropped out, the pipeline would silently grab whatever camera
    # happened to BE at index 0 instead -- often the built-in Mac
    # camera -- and either answer from the wrong image or fail confusingly.
    # The user should never get an answer sourced from a camera other than
    # the intended external one without knowing it. Raise instead of
    # guessing; the caller (main.PressToTalkRecorder.start()) already
    # handles this by speaking a clear failure instead of crashing.
    raise CameraNotFoundError(
        f"No camera matches the external camera's native "
        f"{config.CAMERA_NATIVE_WIDTH}x{config.CAMERA_NATIVE_HEIGHT} fingerprint among "
        f"indices 0..{max_index - 1}. It is likely disconnected -- check the physical "
        f"connection. Refusing to fall back to a different camera (e.g. the built-in one)."
    )


_camera_cap: Optional[cv2.VideoCapture] = None
_camera_lock = threading.Lock()


def _open_camera(camera_index: Optional[int] = None) -> cv2.VideoCapture:
    idx = camera_index if camera_index is not None else resolve_camera_index()
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {idx}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    # only need the full warmup once, right after opening -- autoexposure
    # needs a few frames to settle, but once the device is live and
    # streaming it doesn't need re-settling on every single capture.
    frame = None
    for _ in range(config.CAMERA_WARMUP_FRAMES + 1):
        ok, frame = cap.read()
        if not ok:
            cap.release()
            raise RuntimeError(f"Camera index {idx} opened but frame read failed")
    return cap


def prime_camera() -> None:
    """Open the camera once, up front, so the first press-to-talk capture
    isn't the one paying the (observed: 1-3+ second) cost of opening a
    fresh cv2.VideoCapture and letting autoexposure settle. Call this at
    pipeline startup.

    REVERTED (2026-09-20): tried retry-with-backoff here (and in
    capture_frame()'s reopen path, and spacing out the warmup reads) but a
    live retest immediately produced worse, more confusing behavior than
    this simple version, which is what was running during the confirmed-
    good morning session (11 clean questions in a row). Reverted back to
    exactly that rather than keep layering fixes without a clean
    before/after comparison.
    """
    global _camera_cap
    with _camera_lock:
        if _camera_cap is None:
            _camera_cap = _open_camera()


def capture_frame(camera_index: Optional[int] = None) -> np.ndarray:
    """Grab a frame from the persistent camera handle (opened once via
    prime_camera()/lazily on first use, kept open for the pipeline's
    lifetime).

    Deliberately NOT "open camera, grab frame, close camera" per call --
    that was measured to add 1-3+ seconds of latency to every single
    button press (mostly USB device re-init and autoexposure settling),
    which is a dealbreaker for a press-to-talk UX and was also long enough
    to make PressToTalkRecorder.stop() visibly block behind
    PressToTalkRecorder.start()'s lock if a click landed mid-capture.
    """
    global _camera_cap
    with _camera_lock:
        if _camera_cap is None or camera_index is not None:
            if _camera_cap is not None:
                _camera_cap.release()
            _camera_cap = _open_camera(camera_index)
        ok, frame = _camera_cap.read()
        if not ok:
            # device may have been unplugged/disrupted -- reopen once and retry
            log.warning("camera read failed, reopening")
            _camera_cap.release()
            _camera_cap = _open_camera(camera_index)
            ok, frame = _camera_cap.read()
            if not ok:
                raise RuntimeError("camera read failed even after reopening")
        return frame


def release_camera() -> None:
    global _camera_cap
    with _camera_lock:
        if _camera_cap is not None:
            _camera_cap.release()
            _camera_cap = None


def resolve_mic_device_index() -> Optional[int]:
    """Find the mic by NAME match against config.MIC_NAME_HINT, not by a
    fixed config.MIC_DEVICE_INDEX.

    CONFIRMED on this machine (2026-09-19) that device indices shift here
    the same way camera indices do (see resolve_camera_index()) -- the
    mic and webcam share a USB-C dongle, and after it dropped and came
    back, "AB13X USB Audio" moved from index 4 to index 3. The pipeline
    kept using the stale index 4, which by then pointed at "Background
    Music" (a silent virtual device), producing dead silence -- not bad
    audio quality, literal zero signal (peak=0, rms=0.0). Whisper then
    burned ~36 seconds trying to transcribe true silence before giving up
    with an empty result, which is what actually caused the "why isn't it
    responding in 1-2 seconds" symptom -- it was never a VLM speed
    problem, the pipeline never even reached the VLM step.

    Unlike the camera situation, sounddevice reliably reports real device
    names on macOS, so name matching is enough here -- no resolution
    fingerprint trick needed.
    """
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and config.MIC_NAME_HINT.lower() in d["name"].lower():
            log.info("resolved mic %r at index %d", d["name"], i)
            return i
    # CONFIRMED on real hardware (2026-09-19): falling back to
    # config.MIC_DEVICE_INDEX here produced the exact same class of bug as
    # the camera's old fallback -- when AB13X genuinely wasn't in the
    # device list, that stale index pointed at "Background Music (UI
    # Sounds)", a silent virtual device, producing true digital silence
    # (peak=0, rms=0.0) that Whisper burned tens of seconds failing to
    # transcribe. Unlike the camera (where any other real camera gives a
    # plausible-looking but WRONG answer, worse than failing outright),
    # falling back to `None` here -- sounddevice's "use the system's
    # actual default input device" -- is safe: it will essentially always
    # be a real, live microphone (Bluetooth headset, the built-in mic, etc.), not a
    # silent one, so the user still gets an answer instead of dead audio.
    log.warning(
        "could not find an input device matching MIC_NAME_HINT=%r -- "
        "falling back to the system default input device instead of the "
        "possibly-stale config.MIC_DEVICE_INDEX=%r",
        config.MIC_NAME_HINT, config.MIC_DEVICE_INDEX,
    )
    return None


# ---------------------------------------------------------------------------
# Microphone / press-to-talk recording
# ---------------------------------------------------------------------------

class PressToTalkRecorder:
    """First press: start() begins an open-ended recording on a background
    thread and snapshots a camera frame. Second press: stop() ends it and
    returns (audio, frame). A safety timer auto-stops after
    config.MAX_RECORD_SECONDS if stop() is never called.

    REVERTED (2026-09-20): briefly tried keeping one persistent mic stream
    open for the whole session (to avoid Bluetooth headset' ~8s Bluetooth mic
    warm-up on every press) but a live retest immediately produced worse,
    more confusing behavior than this simple version, which is what was
    running during the confirmed-good morning session (11 clean questions
    in a row). Reverted back to exactly that rather than keep layering
    fixes on top of each other without a clean before/after comparison.
    The AB13X mic (when actually connected) doesn't have this Bluetooth
    warm-up problem at all -- it's specific to falling back to Bluetooth headset,
    which only happens when AB13X isn't found.

    RE-ENABLED (2026-10-18) behind config.MIC_PERSISTENT_STREAM: AB13X is
    gone, Bluetooth headset is now the mic, and its ~2.5s of post-open silence was
    eating the start of every question. See config.py for details.
    """

    def __init__(self):
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._frame_snapshot: Optional[np.ndarray] = None
        self._safety_timer: Optional[threading.Timer] = None
        self._got_live_audio = False
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("audio input status: %s", status)
        if not self._got_live_audio and np.any(indata):
            self._got_live_audio = True
        # with a persistent stream (config.MIC_PERSISTENT_STREAM) the
        # callback runs all session -- only keep audio between the presses
        if self._recording:
            self._frames.append(indata.copy())

    def _stream_alive(self) -> bool:
        # main._reset_portaudio() terminates PortAudio, which kills open
        # streams out from under us -- .active may then be False or raise
        try:
            return self._stream is not None and self._stream.active
        except Exception:
            return False

    def _open_stream(self) -> bool:
        """Open the mic stream (with retries, see MIC_OPEN_RETRIES) and
        wait for it to deliver real audio (see MIC_LIVE_WAIT_SEC).
        Returns False if it couldn't be opened at all."""
        self._close_stream()
        self._got_live_audio = False
        last_error = None
        for attempt in range(config.MIC_OPEN_RETRIES):
            try:
                self._stream = sd.InputStream(
                    samplerate=config.SAMPLE_RATE,
                    channels=config.CHANNELS,
                    dtype=config.RECORDING_DTYPE,
                    device=resolve_mic_device_index(),
                    callback=self._callback,
                )
                self._stream.start()
                last_error = None
                break
            except Exception as e:
                last_error = e
                self._stream = None
                log.warning("mic open attempt %d/%d failed: %s", attempt + 1, config.MIC_OPEN_RETRIES, e)
                _reset_portaudio()
                time.sleep(config.MIC_OPEN_RETRY_DELAY_SEC)
        if last_error is not None:
            log.error("could not open mic after %d attempts: %s", config.MIC_OPEN_RETRIES, last_error)
            return False

        t0 = time.time()
        while not self._got_live_audio and time.time() - t0 < config.MIC_LIVE_WAIT_SEC:
            time.sleep(0.02)
        if self._got_live_audio:
            log.info("mic live after %.2fs", time.time() - t0)
        else:
            log.warning("mic opened but still silent after %.1fs (continuing anyway)", config.MIC_LIVE_WAIT_SEC)
        return True

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass  # already dead, e.g. after _reset_portaudio()
            self._stream = None

    def open_persistent_stream(self) -> None:
        """Call once at pipeline startup when MIC_PERSISTENT_STREAM is on,
        so the mic's warm-up happens before "ready", not on a press."""
        with self._lock:
            self._open_stream()

    def close(self) -> None:
        with self._lock:
            self._recording = False
            self._close_stream()

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            log.info("recording started")
            self._frames = []
            # Start audio capture and play the activation tone FIRST, before
            # touching the camera. capture_frame() opens a fresh
            # cv2.VideoCapture and discards CAMERA_WARMUP_FRAMES frames each
            # time, which is slow enough (observed: noticeable, on the order
            # of a second+) that doing it before the tone made the button
            # feel laggy -- the mic was also not yet recording during that
            # whole window, risking clipping the start of what the user
            # says. Grabbing the frame after the tone means the user gets
            # instant feedback and the mic is already live while the camera
            # opens in the background.
            #
            # CONFIRMED on real hardware (2026-09-19): sd.InputStream() can
            # throw PortAudioError [-9986] intermittently even for a device
            # that opens fine moments later in isolation (this specific mic
            # is on the same flaky USB-C dongle that also dropped the
            # camera/mic index earlier -- root cause not fully pinned down,
            # possibly transient CoreAudio contention). Retry a few times
            # before giving up -- and critically, this used to throw BEFORE
            # play_start_tone() ran, meaning a failed open produced total
            # silence with no feedback at all that anything had gone wrong.
            if not (config.MIC_PERSISTENT_STREAM and self._stream_alive()):
                if config.MIC_PERSISTENT_STREAM and self._stream is not None:
                    log.warning("persistent mic stream died, reopening")
                if not self._open_stream():
                    log.error("giving up on this recording, mic unavailable")
                    play_stop_tone()  # distinct "something's wrong, not recording" cue instead of dead silence
                    return

            self._recording = True
            self._frame_snapshot = None
            play_start_tone()
            self._safety_timer = threading.Timer(config.MAX_RECORD_SECONDS, self._safety_stop)
            self._safety_timer.daemon = True
            self._safety_timer.start()

        # Camera capture deliberately happens AFTER releasing the lock above.
        # CONFIRMED live (2026-09-20): when the Sonix camera drops out,
        # resolve_camera_index()'s full 8-index probe takes 4-8+ seconds --
        # capturing the frame *inside* the lock meant a second button press
        # (meant to stop the recording) sat blocked waiting for that same
        # lock, so its stop tone didn't play until the stuck camera probe
        # gave up. stop() only touches recording state (mic stream, frames,
        # frame_snapshot) under the lock, none of which depends on the
        # camera, so button responsiveness no longer has to wait on it.
        try:
            frame = capture_frame()
        except Exception:
            log.exception("failed to capture camera frame at recording start")
            frame = None
        with self._lock:
            if self._recording:  # only apply if still the same recording session
                self._frame_snapshot = frame

    def _safety_stop(self) -> None:
        log.warning("MAX_RECORD_SECONDS reached, auto-stopping recording")
        self.stop()

    def stop(self) -> Optional[tuple[np.ndarray, Optional[np.ndarray]]]:
        with self._lock:
            if not self._recording:
                return None
            if self._safety_timer:
                self._safety_timer.cancel()
                self._safety_timer = None
            self._recording = False
            if not config.MIC_PERSISTENT_STREAM:
                self._close_stream()
            play_stop_tone()
            log.info("recording stopped")
            if self._frames:
                audio = np.concatenate(self._frames, axis=0)
            else:
                audio = np.zeros((0, config.CHANNELS), dtype=config.RECORDING_DTYPE)
            frame = self._frame_snapshot
            self._frame_snapshot = None
            return audio, frame

    def toggle(self) -> Optional[tuple[np.ndarray, Optional[np.ndarray]]]:
        """Convenience for the button handler: press once starts, press
        again stops and returns the (audio, frame) pair; returns None on the
        press that only started a recording."""
        if self._recording:
            return self.stop()
        self.start()
        return None


# ---------------------------------------------------------------------------
# Trigger button backends
# ---------------------------------------------------------------------------

class ButtonListener:
    """Base interface: calls on_press() (no args) each time the trigger
    fires. Subclasses run their own listening thread."""

    def __init__(self, on_press):
        self.on_press = on_press

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class PynputButtonListener(ButtonListener):
    """Global hotkey listener via pynput. Default backend to try on macOS --
    per the porting notes, it is NOT yet confirmed whether this clicker
    sends a real key event on macOS the way it did not on Windows. Listens
    for every key in config.BUTTON_KEY_CANDIDATES as a trigger, and (in
    --probe mode, via test_button.py) can log every key it sees so the
    right one can be identified and added to that list.
    """

    def __init__(self, on_press, log_all: bool = False):
        super().__init__(on_press)
        self._log_all = log_all
        self._listener = None

    def _candidate_keys(self):
        from pynput import keyboard
        keys = []
        for name in config.BUTTON_KEY_CANDIDATES:
            key = getattr(keyboard.Key, name, None)
            if key is not None:
                keys.append(key)
            else:
                log.warning("BUTTON_KEY_CANDIDATES entry %r is not a known pynput.keyboard.Key", name)
        return keys

    def _on_key_press(self, key):
        if self._log_all:
            log.info("key event: %s", key)
        if key in self._candidate_keys():
            self.on_press()

    def start(self) -> None:
        from pynput import keyboard
        self._listener = keyboard.Listener(on_press=self._on_key_press)
        self._listener.start()
        log.info("pynput button listener started (watching for %s)", config.BUTTON_KEY_CANDIDATES)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None


class CoreAudioVolumeWatchListener(ButtonListener):
    """Polls system output volume and treats a change as a button press.

    SUPERSEDED by MediaKeyTapButtonListener below -- kept only for
    reference/fallback. Confirmed on this machine (2026-09-19): the Bluetooth clicker
    Snapshot 2 clicker sends a real macOS volume-up media-key event, the
    same volume-hijack behavior it had on Windows, just observable here
    instead of hidden. This class can only ever REACT after the system
    volume has already changed (and the volume HUD has already flashed);
    it cannot stop that from happening. MediaKeyTapButtonListener
    intercepts the same event before the OS acts on it and suppresses it,
    which is what actually satisfies "don't touch the volume."
    """

    def __init__(self, on_press, poll_interval: float = 0.1):
        super().__init__(on_press)
        self.poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _get_system_volume(self) -> float:
        # Placeholder: shells out to `osascript` for now (polling-friendly,
        # zero extra deps). If this backend turns out to be needed for real,
        # replace with a direct CoreAudio call via PyObjC for lower latency:
        # AudioObjectGetPropertyData(kAudioObjectSystemObject,
        #   kAudioHardwareServiceDeviceProperty_VirtualMasterVolume, ...)
        out = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())

    def _run(self) -> None:
        last = None
        try:
            last = self._get_system_volume()
        except Exception:
            log.exception("could not read initial system volume")
        while self._running:
            time.sleep(self.poll_interval)
            try:
                current = self._get_system_volume()
            except Exception:
                continue
            if last is not None and current != last:
                log.info("system volume changed %.0f -> %.0f, treating as button press", last, current)
                self.on_press()
            last = current

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Core Audio volume-watch button listener started (polling every %.2fs)", self.poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None


class MediaKeyTapButtonListener(ButtonListener):
    """Intercepts macOS media/volume-key events via a low-level CGEventTap
    and SUPPRESSES the ones matching config.BUTTON_MEDIA_KEY_CODES before
    macOS acts on them -- this is what actually stops the Bluetooth clicker
    clicker from changing the system volume (confirmed on this machine:
    every click sends a real NX_KEYTYPE_SOUND_UP system-defined event,
    keycode 0). Each suppressed key-down transition fires on_press(); the
    matching key-up is suppressed too but not treated as a second press.

    Requires the "Input Monitoring" permission for whatever process runs
    this (Terminal.app, or the python3 binary directly) -- macOS should
    prompt for it the first time CGEventTapCreate() is called; if the tap
    silently fails to create (log shows an error, no presses are ever
    detected), grant it manually in System Settings -> Privacy & Security
    -> Input Monitoring and re-run.

    Reference for the NSEvent data1 bit-packing used below: this is the
    same technique long used by macOS media-key-intercepting utilities
    (e.g. the "SPMediaKeyTap" pattern) -- data1's high 16 bits are the key
    code, and bits 8-15 of the low 16 bits are the up/down state (0x0A =
    down).
    """

    NX_SYSDEFINED = 14          # CGEventType value for "system defined" events (media/volume/brightness keys); not exposed as a named Quartz constant
    NX_SUBTYPE_AUX_CONTROL_BUTTONS = 8

    def __init__(self, on_press, trigger_key_codes=None):
        super().__init__(on_press)
        self.trigger_key_codes = set(config.BUTTON_MEDIA_KEY_CODES if trigger_key_codes is None else trigger_key_codes)
        self._thread: Optional[threading.Thread] = None
        self._run_loop = None
        self._tap = None
        self._last_trigger_time = 0.0

    def _tap_callback(self, proxy, event_type, event, refcon):
        import Quartz
        from AppKit import NSEvent

        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            log.warning("event tap was disabled by the OS (type=%s); re-enabling", event_type)
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        if event_type != self.NX_SYSDEFINED:
            return event

        try:
            ns_event = NSEvent.eventWithCGEvent_(event)
        except Exception:
            return event
        if ns_event is None or ns_event.subtype() != self.NX_SUBTYPE_AUX_CONTROL_BUTTONS:
            return event

        data1 = ns_event.data1()
        key_code = (data1 & 0xFFFF0000) >> 16
        key_flags = data1 & 0x0000FFFF
        key_is_pressed = ((key_flags & 0xFF00) >> 8) == 0x0A

        if key_code not in self.trigger_key_codes:
            return event  # not our key (e.g. brightness, play/pause) -- pass through untouched

        if key_is_pressed:
            now = time.time()
            if now - self._last_trigger_time < config.BUTTON_DEBOUNCE_SEC:
                log.debug("media key code %d pressed -- within debounce window, ignoring (same physical click)", key_code)
            else:
                self._last_trigger_time = now
                log.info("media key code %d pressed -- suppressing system volume change, treating as button press", key_code)
                # Dispatch on_press() on its own thread rather than calling it
                # inline: on_press() can block for a while (tone playback,
                # camera capture in the real pipeline), and this callback runs
                # on the CGEventTap's run loop -- if it doesn't return quickly,
                # macOS disables the tap ("disabled by timeout", observed
                # empirically during testing) and can drop the next click
                # until it's re-enabled.
                threading.Thread(target=self.on_press, daemon=True).start()
        return None  # suppress both down and up so no volume change / HUD flash happens at all

    def _run(self) -> None:
        import Quartz

        event_mask = Quartz.CGEventMaskBit(self.NX_SYSDEFINED)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            event_mask,
            self._tap_callback,
            None,
        )
        if self._tap is None:
            log.error(
                "Failed to create the media-key event tap. This almost always means "
                "Input Monitoring permission hasn't been granted -- go to System "
                "Settings -> Privacy & Security -> Input Monitoring, enable it for "
                "Terminal (or whatever runs main.py), then restart the pipeline."
            )
            return

        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, run_loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        log.info("media-key event tap started (suppressing key codes %s)", self.trigger_key_codes)
        Quartz.CFRunLoopRun()
        log.info("media-key event tap run loop exited")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        import Quartz
        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None


def get_button_listener(on_press, log_all: bool = False) -> ButtonListener:
    if config.BUTTON_BACKEND == "pynput_key":
        return PynputButtonListener(on_press, log_all=log_all)
    elif config.BUTTON_BACKEND == "media_key_tap":
        if not IS_MACOS:
            raise RuntimeError("media_key_tap backend is macOS-only")
        return MediaKeyTapButtonListener(on_press)
    elif config.BUTTON_BACKEND == "core_audio_watch":
        if not IS_MACOS:
            raise RuntimeError("core_audio_watch backend is macOS-only")
        return CoreAudioVolumeWatchListener(on_press)
    else:
        raise ValueError(f"Unknown BUTTON_BACKEND: {config.BUTTON_BACKEND!r}")


# ---------------------------------------------------------------------------
# Speech-to-text (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("loading Whisper model %r (compute_type=%s, device=%s)...",
                  config.WHISPER_MODEL_SIZE, config.WHISPER_COMPUTE_TYPE, config.WHISPER_DEVICE)
        _whisper_model = WhisperModel(
            config.WHISPER_MODEL_SIZE,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
    return _whisper_model


def transcribe_audio(audio: np.ndarray) -> str:
    """audio: int16 mono numpy array at config.SAMPLE_RATE, shape (N, channels)."""
    model = _get_whisper()
    samples = audio.astype(np.float32).reshape(-1)
    if config.RECORDING_DTYPE == "int16":
        samples /= 32768.0

    # CONFIRMED on real hardware (2026-09-19): the AB13X mic's input level
    # is low even when clipped on near the mouth -- captured speech peaked
    # at only ~20% of full scale, which faster-whisper's VAD/speech
    # detector rejected outright as non-speech ("VAD filter removed
    # 00:08.000 of audio"), producing an empty transcription despite clear,
    # audible speech being present. Digitally boosting the SAME clip to
    # ~90% of full scale before transcribing produced a correct result.
    # Peak-normalize here so this doesn't depend on macOS's input-level
    # setting for this specific device being right.
    if config.MIC_AUTO_GAIN_ENABLED:
        peak = np.abs(samples).max()
        if peak > 0:
            gain = min(config.MIC_AUTO_GAIN_TARGET_PEAK / peak, config.MIC_AUTO_GAIN_MAX)
            samples = samples * gain

    segments, info = model.transcribe(samples, language=config.WHISPER_LANGUAGE)
    allowed = config.WHISPER_ALLOWED_LANGUAGES
    if config.WHISPER_LANGUAGE is None and allowed and info.language not in allowed:
        # see WHISPER_ALLOWED_LANGUAGES in config.py
        probs = dict(info.all_language_probs or [])
        lang = max(allowed, key=lambda l: probs.get(l, 0.0))
        log.info("detected language %r not allowed, re-transcribing as %r", info.language, lang)
        segments, info = model.transcribe(samples, language=lang)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


# ---------------------------------------------------------------------------
# Question routing
# ---------------------------------------------------------------------------

def classify_question(question: str) -> str:
    """Plain keyword/substring routing, checked in priority order -- see
    config.MEDICAL_TRIGGER_PHRASES / config.OCR_TRIGGER_PHRASES and the
    README's 'dual pipeline' section for why this exists as a hard either/or
    rather than letting the VLM handle everything. Returns "medical", "ocr",
    or "vlm"."""
    q = question.lower()
    if any(phrase in q for phrase in config.MEDICAL_TRIGGER_PHRASES):
        return "medical"
    if any(phrase in q for phrase in config.OCR_TRIGGER_PHRASES):
        return "ocr"
    return "vlm"


def is_reading_request(question: str) -> bool:
    """Back-compat helper: True for either OCR-routed case (plain reading/
    transit, or medical). Prefer classify_question() for new code."""
    return classify_question(question) in ("ocr", "medical")


# ---------------------------------------------------------------------------
# OCR (EasyOCR)
# ---------------------------------------------------------------------------

_ocr_reader = None


def _get_ocr():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        log.info("loading EasyOCR reader (languages=%s)...", config.OCR_LANGUAGES)
        _ocr_reader = easyocr.Reader(config.OCR_LANGUAGES, gpu=config.OCR_GPU)
    return _ocr_reader


def _extract_text(frame: np.ndarray) -> str:
    """Shared EasyOCR text extraction used by both ask_ocr() and
    ask_medical_record()."""
    reader = _get_ocr()
    results = reader.readtext(frame)
    texts = [text for (_bbox, text, _conf) in results]
    return " ".join(texts)


def ask_ocr(frame: np.ndarray, question: str) -> str:
    text = _extract_text(frame)
    if not text:
        return "I don't see any readable text."
    return text


def _lookup_medical_record_db(question: str, ocr_text: str) -> Optional[str]:
    """Placeholder for the future patient-record database sync (see
    config.MEDICAL_DB_ENABLED / config.MEDICAL_DB_URL). Once a real database
    exists, this should look up a precise, verified answer (e.g. by matching
    a prescription ID or patient name found in ocr_text against known
    records) instead of main.ask_medical_record() falling back to reading
    back raw OCR text. Deliberately not implemented yet -- there's no
    database to query, and speculative parsing logic here would just be
    guessing at a schema that doesn't exist."""
    if not config.MEDICAL_DB_ENABLED:
        return None
    raise NotImplementedError("MEDICAL_DB_ENABLED is True but no database client is wired up yet")


def ask_medical_record(frame: np.ndarray, question: str) -> str:
    """Medical-record reading route (prescription labels, medication
    instructions, lab results, appointment slips). Uses the same EasyOCR
    engine as ask_ocr() today; kept as a separate function so the future
    database sync (_lookup_medical_record_db) only needs one call site to
    change, and so this path can eventually be verified/phrased differently
    from a plain sign-reading answer without touching that path."""
    text = _extract_text(frame)
    db_answer = _lookup_medical_record_db(question, text)
    if db_answer is not None:
        return db_answer
    if not text:
        return "I don't see any readable text on this."
    return text


# ---------------------------------------------------------------------------
# Vision-language model backends
# ---------------------------------------------------------------------------

_vlm_handle = None  # (backend_name, model_object[, extra])


def _get_vlm():
    global _vlm_handle
    if _vlm_handle is not None and _vlm_handle[0] == config.VLM_BACKEND:
        return _vlm_handle

    backend = config.VLM_BACKEND
    if backend == "smolvlm":
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import MtmdChatHandler

        log.info("loading SmolVLM2 GGUF (%s)...", config.SMOLVLM_MODEL_FILE)
        model_path = hf_hub_download(config.SMOLVLM_REPO, config.SMOLVLM_MODEL_FILE)
        mmproj_path = hf_hub_download(config.SMOLVLM_REPO, config.SMOLVLM_MMPROJ_FILE)
        chat_handler = MtmdChatHandler(clip_model_path=mmproj_path)
        llm = Llama(
            model_path=model_path,
            chat_handler=chat_handler,
            n_ctx=config.SMOLVLM_N_CTX,
            n_gpu_layers=config.SMOLVLM_GPU_LAYERS,
            verbose=False,
        )
        _vlm_handle = (backend, llm)

    elif backend == "qwen2vl":
        import torch
        from transformers import Qwen2VLForConditionalGeneration, AutoProcessor

        device = config.TRANSFORMERS_DEVICE if torch.backends.mps.is_available() else "cpu"
        log.info("loading %s on device=%s...", config.QWEN2VL_MODEL_ID, device)
        processor = AutoProcessor.from_pretrained(config.QWEN2VL_MODEL_ID)
        model = Qwen2VLForConditionalGeneration.from_pretrained(
            config.QWEN2VL_MODEL_ID,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        ).to(device)
        model.eval()
        _vlm_handle = (backend, model, processor)

    elif backend == "qwen25vl":
        # Newer generation than qwen2vl, meaningfully more accurate at
        # comparable size (2026-09-19 user request: "much better acc even
        # if slower", 4s total round-trip ceiling). 7B was tested at fp32
        # and OOM'd on this Mac's MPS pool (~20GB ceiling, 7B needs ~28GB
        # in fp32) -- but the pipeline itself uses fp16 here (as qwen2vl
        # already does above), which roughly halves that, so 7B may still
        # be viable through the real pipeline even though the fp32
        # standalone test wasn't. Benchmark both 3B and 7B for real before
        # deciding; config.QWEN25VL_MODEL_ID picks which.
        import torch
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

        device = config.TRANSFORMERS_DEVICE if torch.backends.mps.is_available() else "cpu"
        log.info("loading %s on device=%s...", config.QWEN25VL_MODEL_ID, device)
        processor = AutoProcessor.from_pretrained(config.QWEN25VL_MODEL_ID)
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            config.QWEN25VL_MODEL_ID,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        ).to(device)
        model.eval()
        _vlm_handle = (backend, model, processor)

    elif backend == "moondream":
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = config.TRANSFORMERS_DEVICE if torch.backends.mps.is_available() else "cpu"
        log.info("loading %s on device=%s...", config.MOONDREAM_MODEL_ID, device)
        tokenizer = AutoTokenizer.from_pretrained(config.MOONDREAM_MODEL_ID, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            config.MOONDREAM_MODEL_ID, trust_remote_code=True,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        ).to(device)
        model.eval()
        _vlm_handle = (backend, model, tokenizer)

    else:
        raise ValueError(f"Unknown VLM_BACKEND: {backend!r}")

    return _vlm_handle


def _frame_to_jpeg_data_uri(frame: np.ndarray) -> str:
    import base64
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("failed to encode frame as JPEG")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _ask_smolvlm(llm, frame: np.ndarray, question: str) -> str:
    data_uri = _frame_to_jpeg_data_uri(frame)
    prompt = config.VLM_PROMPT_TEMPLATE.format(question=question)
    response = llm.create_chat_completion(
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": prompt},
            ],
        }],
        max_tokens=config.SMOLVLM_MAX_TOKENS,
    )
    return response["choices"][0]["message"]["content"].strip()


def _ask_qwen2vl(model, processor, frame: np.ndarray, question: str) -> str:
    from PIL import Image
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    prompt = config.VLM_PROMPT_TEMPLATE.format(question=question)
    messages = [{
        "role": "user",
        "content": [
            {"type": "image"},
            {"type": "text", "text": prompt},
        ],
    }]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[image], padding=True, return_tensors="pt").to(model.device)
    output_ids = model.generate(**inputs, max_new_tokens=config.VLM_MAX_NEW_TOKENS)
    trimmed = [out[len(inp):] for inp, out in zip(inputs["input_ids"], output_ids)]
    result = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
    return result.strip()


def _ask_moondream(model, tokenizer, frame: np.ndarray, question: str) -> str:
    from PIL import Image
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    prompt = config.VLM_PROMPT_TEMPLATE.format(question=question)
    # moondream2 ships a custom generation API rather than plain
    # model.generate(); newer revisions expose model.query(), older ones
    # only expose encode_image()+answer_question(). Try both.
    if hasattr(model, "query"):
        return model.query(image, prompt)["answer"].strip()
    enc_image = model.encode_image(image)
    return model.answer_question(enc_image, prompt, tokenizer).strip()


def ask_vlm(frame: np.ndarray, question: str) -> str:
    # CONFIRMED on real hardware (2026-09-19): the vision-encoder cost
    # scales with input resolution, and feeding it the raw camera frame
    # (config.CAMERA_WIDTH x CAMERA_HEIGHT, 1280x720) was costing 9.19s
    # per question -- resizing the SAME frame down to
    # VLM_INPUT_WIDTH x VLM_INPUT_HEIGHT (640x360) before inference cut
    # that to 1.88s with an identical answer on that test. This is why
    # earlier benchmarks (using small synthetic 800x400 test images) never
    # showed this: real camera frames are meaningfully bigger. Only the
    # VLM path is resized here -- OCR (main.ask_ocr()) keeps the full
    # frame, since text legibility benefits from resolution in a way scene
    # understanding doesn't need as much of.
    if config.VLM_INPUT_WIDTH and config.VLM_INPUT_HEIGHT:
        frame = cv2.resize(frame, (config.VLM_INPUT_WIDTH, config.VLM_INPUT_HEIGHT))
    handle = _get_vlm()
    backend = handle[0]
    if backend == "smolvlm":
        return _ask_smolvlm(handle[1], frame, question)
    elif backend in ("qwen2vl", "qwen25vl"):
        # Qwen2.5-VL uses the identical chat-template/processor API as
        # Qwen2-VL -- same function works for both.
        return _ask_qwen2vl(handle[1], handle[2], frame, question)
    elif backend == "moondream":
        return _ask_moondream(handle[1], handle[2], frame, question)
    else:
        raise ValueError(f"Unknown VLM_BACKEND: {backend!r}")


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------

_pyttsx3_engine = None


def _speak_pyttsx3(text: str) -> None:
    global _pyttsx3_engine
    import pyttsx3
    if _pyttsx3_engine is None:
        _pyttsx3_engine = pyttsx3.init()
        _pyttsx3_engine.setProperty("rate", config.TTS_RATE)
        if config.TTS_VOICE_HINT_PYTTSX3:
            for voice in _pyttsx3_engine.getProperty("voices"):
                if config.TTS_VOICE_HINT_PYTTSX3.lower() in voice.name.lower():
                    _pyttsx3_engine.setProperty("voice", voice.id)
                    break
    _pyttsx3_engine.say(text)
    _pyttsx3_engine.runAndWait()


def _speak_say(text: str) -> None:
    # Leading silence marker (macOS speech-synthesis embedded command,
    # supported by `say`/NSSpeechSynthesizer): reported on real hardware
    # (2026-09-19) that spoken answers "start mid-sentence" about half the
    # time over Bluetooth headset. Root cause: answers are spoken after a multi-
    # second silent processing gap (STT+VLM), and Bluetooth audio links
    # commonly clip the first fraction of a second of audio when output
    # resumes after being idle, while the link re-activates. A short
    # leading silence gives that re-activation somewhere to happen that
    # isn't the start of the actual answer.
    if config.TTS_LEADING_SILENCE_MS > 0:
        text = f"[[slnc {config.TTS_LEADING_SILENCE_MS}]]{text}"
    cmd = ["say", "-r", str(config.TTS_RATE)]
    if config.TTS_VOICE:
        cmd += ["-v", config.TTS_VOICE]
    cmd.append(text)
    subprocess.run(cmd, check=True)


def speak(text: str) -> None:
    log.info("speaking: %s", text)
    if config.TTS_BACKEND == "say":
        if not IS_MACOS:
            raise RuntimeError("TTS_BACKEND='say' requires macOS")
        _speak_say(text)
    elif config.TTS_BACKEND == "pyttsx3":
        _speak_pyttsx3(text)
    else:
        raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND!r}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PipelineState:
    questions_answered: int = 0
    last_question: Optional[str] = None
    last_answer: Optional[str] = None
    last_route: Optional[str] = None  # "medical" | "ocr" | "vlm"


class CogniSeePipeline:
    def __init__(self):
        self.state = PipelineState()
        self.recorder = PressToTalkRecorder()
        self._process_queue: "queue.Queue[tuple[np.ndarray, Optional[np.ndarray]]]" = queue.Queue()
        self.button = get_button_listener(self._on_button_press)

    def _on_button_press(self) -> None:
        result = self.recorder.toggle()
        if result is not None:
            audio, frame = result
            self._process_queue.put((audio, frame))

    def _handle_one(self, audio: np.ndarray, frame: Optional[np.ndarray]) -> None:
        if frame is None:
            speak("Sorry, I couldn't see anything. Please try again.")
            return
        if audio.shape[0] == 0:
            speak("Sorry, I didn't hear anything. Please try again.")
            return

        question = transcribe_audio(audio)
        log.info("transcribed question: %r", question)
        if not question.strip():
            speak("Sorry, I didn't catch that. Please try again.")
            return

        route = classify_question(question)
        if route == "medical":
            answer = ask_medical_record(frame, question)
        elif route == "ocr":
            answer = ask_ocr(frame, question)
        else:
            answer = ask_vlm(frame, question)

        log.info("[%s] Q: %r -> A: %r", route, question, answer)
        self.state.questions_answered += 1
        self.state.last_question = question
        self.state.last_answer = answer
        self.state.last_route = route
        speak(answer)

    def _prime_models(self) -> None:
        """Force-load Whisper and the VLM, plus one throwaway inference
        call each, before announcing "ready" -- both models load lazily on
        first use otherwise, and the first real question would eat that
        one-time cost (model load + first-call kernel warmup) on top of
        actual inference. Steady-state round-trip timing (what
        "answer in 2.5s" actually means) is meaningless if the very first
        question is also paying for this.
        """
        log.info("warming up Whisper...")
        silent_audio = np.zeros((config.SAMPLE_RATE // 4, config.CHANNELS), dtype=config.RECORDING_DTYPE)
        transcribe_audio(silent_audio)
        log.info("warming up %s...", config.VLM_BACKEND)
        try:
            frame = capture_frame()
            ask_vlm(frame, "warmup")
        except Exception:
            log.exception("VLM warmup failed (continuing anyway)")

    def run(self) -> None:
        log.info("CogniSee starting. Button backend=%s, VLM backend=%s",
                  config.BUTTON_BACKEND, config.VLM_BACKEND)
        log.info("opening camera...")
        prime_camera()  # pay the one-time camera-open cost now, not on the first button press
        self._prime_models()
        if config.MIC_PERSISTENT_STREAM:
            log.info("opening persistent mic stream...")
            self.recorder.open_persistent_stream()
        self.button.start()
        try:
            speak("CogniSee ready.")
            while True:
                audio, frame = self._process_queue.get()
                try:
                    self._handle_one(audio, frame)
                except Exception:
                    log.exception("error handling question")
                    speak("Sorry, something went wrong.")
        except KeyboardInterrupt:
            log.info("shutting down")
        finally:
            self.button.stop()
            self.recorder.close()
            release_camera()


_instance_lock_file = None


def _acquire_single_instance_lock() -> None:
    """Refuse to start if another CogniSee pipeline is already running.

    CONFIRMED live (2026-10-18): a pipeline whose launching shell was
    killed kept running invisibly, still holding the media-key tap -- so
    every clicker press ALSO toggled a recording (and spoke answers) in
    that hidden copy, during two later test sessions. An OS-level file
    lock is released automatically when the process dies, however it dies,
    so a crash can never leave a stale lock behind.
    """
    import fcntl
    global _instance_lock_file
    path = os.path.join(config.BASE_DIR, ".cognisee.lock")
    _instance_lock_file = open(path, "a+")
    try:
        fcntl.flock(_instance_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _instance_lock_file.seek(0)
        pid = _instance_lock_file.read().strip() or "unknown"
        sys.exit(f"CogniSee is already running (pid {pid}). Stop it first: kill {pid}")
    _instance_lock_file.seek(0)
    _instance_lock_file.truncate()
    _instance_lock_file.write(str(os.getpid()))
    _instance_lock_file.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="CogniSee live pipeline")
    parser.parse_args()
    _acquire_single_instance_lock()
    pipeline = CogniSeePipeline()
    pipeline.run()


if __name__ == "__main__":
    main()
