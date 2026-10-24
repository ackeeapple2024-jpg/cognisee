"""
CogniSee configuration.

Every tunable knob for the pipeline lives here, in one place, so the
hardware-specific parts of main.py never need touching once this file is
correct for the machine you're running on.

Reconstructed for this Mac (2026-09-19). Values below are
starting points, not measured facts about THIS machine yet -- several are
marked "VERIFY" and must be confirmed by running the corresponding
test_*.py script before the live pipeline (main.py) is trusted.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLES_DIR = os.path.join(BASE_DIR, "samples")
MODELS_DIR = os.path.join(BASE_DIR, "models")

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
# SWAPPED 2026-09-23: the Sonix HD Camera (see git history / old comments
# below the fold for its resolution-fingerprint story) was retired after
# repeated live sessions showed its USB-C dongle intermittently dropping
# the video stream mid-session -- confirmed reproducible across multiple
# process restarts, not a one-off. Replaced with a small UVC USB camera
# camera (`system_profiler SPCameraDataType` reports it as "HD camera ",
# ) on a direct connection, not a shared hub.
# A 5-minute continuous-read stability test (293/293 reads ok, 0 reopens
# needed) showed zero drops, versus the Sonix camera failing repeatedly
# within minutes on the same kind of test. Known tradeoff: this camera is
# built for close-focus/macro use (great for reading labels up close, the
# OCR/medical route) and produces visibly blurry frames at normal
# room-scale distance (the VLM route's typical use case) -- user confirmed
# by eye the FOV itself (~70 deg) is fine, so this is accepted as-is for
# now rather than continuing to camera-shop.
#
# Fingerprint approach unchanged from the Sonix era: this camera's native
# 640x480 does not collide with the FaceTime HD camera or either iPhone's
# Continuity Camera (both report 1920x1080 as native) on this laptop, so
# it's still a reliable resolve_camera_index() fingerprint. See git
# history for the two false starts that led to fingerprint-based
# resolution (vs. a fixed index) in the first place -- that reasoning
# still applies to any camera, not just the Sonix one.
#
# CAMERA_INDEX below is UNUSED as a fallback (changed 2026-09-19): it used
# to be returned when the resolution probe found no match, but on real
# hardware that meant a dropped webcam silently got replaced with
# whatever camera happened to be at this index -- often the built-in
# Mac camera -- producing answers sourced from the wrong camera
# without any indication that had happened. main.resolve_camera_index()
# now raises main.CameraNotFoundError instead of falling back to this
# value at all. Kept only for reference/tooling that might want a
# starting guess (e.g. test_camera.py), never trusted by the live pipeline.
CAMERA_INDEX = 0
CAMERA_NAME_HINT = "HD camera"  # substring used only if the backend can report names -- macOS/AVFoundation doesn't reliably expose this via OpenCV, kept for reference only
CAMERA_NATIVE_WIDTH = 640    # USB camera's native resolution -- see resolve_camera_index()
CAMERA_NATIVE_HEIGHT = 480   # used as a fingerprint to find the right device regardless of index
CAMERA_WIDTH = 640    # operating resolution requested once the right device is found (not used for identification) -- capped at native, this camera doesn't go higher
CAMERA_HEIGHT = 480
CAMERA_WARMUP_FRAMES = 5  # frames discarded while auto-exposure settles before capturing the "real" one

# ---------------------------------------------------------------------------
# Microphone / audio input
# ---------------------------------------------------------------------------
# CONFIRMED via test_mic.py --list / --record (2026-09-19): unlike Windows,
# macOS lists only ONE "AB13X USB Audio" device (index 4) -- no silent
# phantom duplicate to rule out here. Verified it's actually live (not
# dead/silent) by recording 4s and checking signal level: peak=3165,
# rms=172.2 out of a possible +-32768 -- real ambient signal, not silence.
# If the mic is ever unplugged/replugged, re-verify rather than trusting
# this index blindly (the device list order isn't guaranteed stable).
MIC_DEVICE_INDEX = 4  # fallback only -- main.resolve_mic_device_index() finds the mic by MIC_NAME_HINT at runtime, see its docstring for why (this index went stale mid-session on real hardware)
# SWAPPED 2026-10-18: AB13X ("USB Audio") is no longer available -- using
# the Bluetooth headset mic explicitly instead of via the default-device fallback.
MIC_NAME_HINT = "AirPods"
SAMPLE_RATE = 16000
CHANNELS = 1
RECORDING_DTYPE = "int16"

# CONFIRMED on real hardware (2026-09-19): the AB13X mic's input level is
# low even clipped on near the mouth -- captured speech peaked at only
# ~20% of int16 full scale, which made faster-whisper's speech detector
# reject it outright as non-speech (empty transcription despite clear,
# audible content). main.transcribe_audio() peak-normalizes toward
# MIC_AUTO_GAIN_TARGET_PEAK before transcribing; MIC_AUTO_GAIN_MAX caps
# the multiplier so near-silence doesn't get blown up into loud noise.
MIC_AUTO_GAIN_ENABLED = True
MIC_AUTO_GAIN_TARGET_PEAK = 0.9  # fraction of full scale to normalize toward
MIC_AUTO_GAIN_MAX = 20.0  # cap on the gain multiplier

# CONFIRMED on real hardware (2026-09-19): opening the mic's InputStream
# intermittently throws PortAudioError [-9986] even though the same
# device opens fine moments later in isolation -- retry a few times
# before giving up rather than silently failing (this used to fail BEFORE
# the activation tone played, so a failed open meant total silence with
# no indication anything had gone wrong).
MIC_OPEN_RETRIES = 3
MIC_OPEN_RETRY_DELAY_SEC = 0.3

# PERSISTENT MIC STREAM (re-enabled 2026-10-18): the Bluetooth headset Bluetooth mic
# delivers pure digital silence (exact zeros) for ~2.5s after every fresh
# InputStream open -- measured on 4/4 saved recordings, which cost the
# start of every question. (Earlier, 2026-09-20, this was measured at ~8s
# and a persistent-stream fix was tried and reverted; the AB13X it was
# reverted in favor of is no longer available.) With this on, the stream
# is opened ONCE at startup and kept open; the button still starts/stops
# each question, and only audio between the two presses is kept. Likely
# reason the 2026-09-20 attempt misbehaved: main._reset_portaudio()
# (called on any tone-playback failure) terminates PortAudio, killing
# every open stream -- the recorder now detects a dead stream and reopens
# it. Tradeoff: Bluetooth headset stay in their headset/call profile all session,
# so spoken answers sound more phone-call-like. False = old per-press
# open/close behavior.
MIC_PERSISTENT_STREAM = True

# After (re)opening the mic, wait up to this long for the first non-zero
# sample before playing the start tone / announcing ready, so the start
# tone always means "the mic is actually live now" -- never "the mic is
# still warming up and will drop what you say first".
MIC_LIVE_WAIT_SEC = 10.0

# Press-to-talk safety timer: if no second button press stops the recording,
# auto-stop after this many seconds.
MAX_RECORD_SECONDS = 30

# ---------------------------------------------------------------------------
# Audio output (TTS + UI tones)
# ---------------------------------------------------------------------------
# "pyttsx3" uses the OS-native speech engine via the pyttsx3 wrapper
# (nsss driver on macOS, sapi5 on Windows) -- one code path, both platforms.
# "say" shells out to the macOS `say` command directly (macOS only, simplest,
# often the most natural-sounding system voices). Try "say" first on this
# Mac; fall back to "pyttsx3" if it's ever not fast/reliable enough.
TTS_BACKEND = "say"
TTS_VOICE = None      # e.g. "Samantha"; None = system default voice. `say -v ?` lists options.
TTS_RATE = 190         # words per minute (used by both backends)
TTS_VOICE_HINT_PYTTSX3 = None  # substring match against pyttsx3 voice names, if TTS_BACKEND == "pyttsx3"

# CONFIRMED on real hardware (2026-09-19): spoken answers over Bluetooth headset
# started mid-sentence about half the time -- a Bluetooth audio link
# commonly clips the first fraction of a second of output when it resumes
# after being idle (here, the multi-second silent STT+VLM processing gap
# before speak() is called). A short leading silence (main._speak_say()
# uses the `[[slnc N]]` embedded speech command) gives the link somewhere
# to re-activate that isn't the start of the real answer. Only implemented
# for the "say" backend; tune this value down if it feels sluggish, up if
# clipping is still audible.
TTS_LEADING_SILENCE_MS = 400

# Confirmation tones (replaces winsound.Beep, which is Windows-only stdlib).
# Generated on the fly as sine waves and played via sounddevice, which is
# already a dependency for mic capture -- no new library needed.
#
# CRITICAL BUG FOUND ON REAL HARDWARE (2026-09-19): tone playback initially
# reused SAMPLE_RATE (16000Hz, chosen for Whisper's expected mic input
# rate) for OUTPUT too. Bluetooth headset over Bluetooth doesn't support 16000Hz
# output directly, so sd.play() threw PortAudioError [-9986] every time --
# meaning every single chime silently crashed instead of just being quiet.
# Worse: because that exception was uncaught inside the button-press
# thread, it also skipped everything AFTER the tone call in
# PressToTalkRecorder.start()/stop() -- camera frame capture and the
# safety timer on start(), stream cleanup ordering on stop(). A tone
# output-rate bug was silently breaking real recording functionality.
# Fixed two ways: (1) tones now use their own TONE_SAMPLE_RATE, decoupled
# from the mic's SAMPLE_RATE -- 44100Hz is close to universally supported
# by output devices, unlike 16000Hz; (2) play_start_tone()/play_stop_tone()
# calls in main.py are now wrapped so a tone failure can never again skip
# the recording logic around it, belt-and-suspenders on top of the rate fix.
TONE_SAMPLE_RATE = 44100
TONE_START_FREQ_HZ = 880
TONE_STOP_FREQ_HZ = 440
TONE_DURATION_SEC = 0.12
TONE_VOLUME = 0.3  # 0.0-1.0
# Same Bluetooth idle-clipping problem as TTS_LEADING_SILENCE_MS above,
# but worse for a 0.12s beep: the clipped part can be the whole tone
# (reported 2026-10-18: start chime never audible over Bluetooth headset). Silence
# prepended to each tone gives the link time to wake up first.
TONE_LEADING_SILENCE_SEC = 0.25

# ---------------------------------------------------------------------------
# Trigger button (Bluetooth clicker)
# ---------------------------------------------------------------------------
# CONFIRMED on this machine (2026-09-19): same failure mode as Windows, just
# observable instead of hidden. Every click sends a real macOS system-level
# "volume up" media-key event (NX_KEYTYPE_SOUND_UP, keycode 0) -- which is
# why plain pynput never saw anything (pynput only sees normal keyDown
# events, not these NSSystemDefined media-key events) while the system
# volume audibly/visibly changed anyway. Fix: intercept and SUPPRESS that
# specific event before macOS acts on it, via a low-level CGEventTap
# (main.MediaKeyTapButtonListener) -- not just detect it after the fact
# (main.CoreAudioVolumeWatchListener, kept only as a reference/fallback).
#
#   "media_key_tap"  -- CGEventTap intercepting + suppressing the clicker's
#                       volume-key event. Current default; this is what
#                       actually stops the volume from changing.
#   "pynput_key"      -- plain global hotkey listener. Confirmed NOT to see
#                       this clicker's presses at all; kept in case a
#                       future/different button does send normal key events.
#   "core_audio_watch" -- polls system volume and reacts after it's already
#                       changed. Cannot prevent the volume change or HUD
#                       flash; superseded by media_key_tap, kept for reference.
BUTTON_BACKEND = "media_key_tap"

# CONFIRMED via test_button.py --probe-media-keys (2026-09-19): this
# clicker sends BOTH NX_KEYTYPE_SOUND_UP (0) and NX_KEYTYPE_SOUND_DOWN (1)
# -- not just volume-up. Both must be suppressed or the volume-down code
# would still change the system volume even with 0 handled.
BUTTON_MEDIA_KEY_CODES = [0, 1]

# Each physical click was observed firing ~2 key-press transitions in rapid
# succession (11 events for ~5 spaced-out clicks) -- a HID double-report
# artifact of this specific clicker, not 2 real presses. Any second trigger
# within this window of the first is treated as part of the same physical
# click and ignored, so one click doesn't produce two start/stop toggles.
BUTTON_DEBOUNCE_SEC = 0.4

# Candidate keys for the "pynput_key" fallback backend, unused by default.
BUTTON_KEY_CANDIDATES = ["media_play_pause", "media_volume_up", "media_volume_down"]

# ---------------------------------------------------------------------------
# Speech-to-text (Whisper, via faster-whisper / CTranslate2)
# ---------------------------------------------------------------------------
# faster-whisper (not openai-whisper) chosen for this rebuild: same Whisper
# weights, CTranslate2 backend, meaningfully faster on both CPU and Apple
# Silicon. If you specifically need bit-identical behavior to whatever ran
# on the Windows box and that was openai-whisper, say so and this should be
# swapped -- flagging this as a judgment call, not a verified port.
# BENCHMARKED on real hardware (2026-09-19), warmed-up steady state on a
# clean 1.3s reference clip: tiny=0.26s, base=0.50s, small=1.49s -- all
# transcribed correctly on that clean clip. Briefly raised to "small" after
# real-world transcription quality complaints, but that test run also had
# a second GPU-heavy process (a VLM benchmark) running concurrently on the
# same Mac, which caused massive contention-driven latency spikes (VLM
# step alone hit 8-22s, vs. its normal ~1.9-2.6s) -- that confound was
# never cleanly separated from Whisper "small"'s own cost before reverting
# back to "base" per user request. If real-world transcription quality
# complaints resurface, re-test "small" in isolation (nothing else running)
# before concluding it's too slow -- the last measurement wasn't clean.
WHISPER_MODEL_SIZE = "base"   # tiny / base / small / medium / large-v3
WHISPER_LANGUAGE = None         # None = auto-detect (needs to cover English + Korean)
# With auto-detect, short/clipped clips were detected as Polish/Norwegian
# (2026-10-18) and transcribed as gibberish in that language. If the
# detected language isn't in this list, re-transcribe forced to the
# most-likely language that IS in it.
WHISPER_ALLOWED_LANGUAGES = ["en", "ko"]
WHISPER_COMPUTE_TYPE = "int8"   # faster-whisper compute type; "int8" is CPU-friendly, fine on Apple Silicon too
WHISPER_DEVICE = "cpu"          # faster-whisper's CTranslate2 backend has no Metal support yet; CPU on Apple Silicon is still fast for "small"

# ---------------------------------------------------------------------------
# Question routing: medical vs. OCR vs. VLM
# ---------------------------------------------------------------------------
# Substring match (lowercased) against the transcribed question, checked in
# this order: MEDICAL_TRIGGER_PHRASES first, then OCR_TRIGGER_PHRASES, else
# VLM. Both trigger lists route to the same EasyOCR engine underneath --
# "medical" is a separate list (not folded into OCR_TRIGGER_PHRASES) only so
# main.ask_medical_record() can be called instead of main.ask_ocr(), which
# is where the future database-sync hook lives (see MEDICAL_DB_* below).
# Keep both lists in sync with README's "dual pipeline" section if you add
# to them -- together they ARE the routing logic.
OCR_TRIGGER_PHRASES = [
    # direct reading requests
    "read this", "read it", "read that", "read the sign", "read the text",
    "read the label", "read the menu", "what does this say", "what does it say",
    "what's written", "what is written",
    # public transit / navigation (small VLMs were unreliable on these, see README)
    "which exit", "what exit", "exit number", "which platform", "what platform",
    "what line", "which line", "what train", "which train",
    "what bus", "which bus", "bus number", "what number bus",
    "what stop", "which stop", "what station", "which station",
    "next stop", "next station", "is this my stop",
    "what time does", "when does the bus", "when does the train",
    "arrival time", "departure time", "schedule",
    "which direction", "which way", "wrong direction",
    "transfer", "which gate", "what gate",
    # Korean equivalents
    "읽어줘", "읽어 줘", "뭐라고 써", "몇 번 출구", "무슨 역", "몇 번 승강장",
    "몇 호선", "무슨 노선", "몇 번 버스", "무슨 버스", "다음 역", "이번 역",
    "여기서 내려", "몇 시에 출발", "몇 시에 도착", "시간표", "환승",
    "몇 번 게이트", "어느 방향",
]

# Medical records/documents (prescription labels, medication instructions,
# lab result printouts, appointment slips). Routes to ask_medical_record(),
# not ask_ocr() -- same OCR engine underneath today, but a separate function
# so the future database sync (MEDICAL_DB_ENABLED below) only has to hook
# into one place, and so medical answers can eventually be phrased/validated
# differently from "read this sign" without touching the transit/reading path.
MEDICAL_TRIGGER_PHRASES = [
    "medication", "medicine", "prescription", "pill bottle", "dosage",
    "dose", "how many pills", "how much medicine", "when do i take",
    "lab result", "lab results", "blood test", "test result",
    "doctor's note", "medical record", "appointment", "refill",
    "side effects", "expiration date on this medicine",
    # Korean equivalents
    "약", "처방전", "복용법", "복용량", "몇 알", "하루에 몇 번",
    "검사 결과", "혈액 검사", "진단서", "의사 소견", "예약", "재처방",
    "부작용", "유효기간",
]

# ---------------------------------------------------------------------------
# Medical record database sync (not implemented yet)
# ---------------------------------------------------------------------------
# Placeholder for a future integration: once there's a real patient-record
# database, main._lookup_medical_record_db() should query it (e.g. by
# matching OCR'd text like a prescription ID or patient name against known
# records) and return a precise, verified answer instead of just reading
# back raw OCR text. Until MEDICAL_DB_ENABLED is True, main.ask_medical_record()
# always falls back to plain OCR -- do not build speculative parsing logic
# ahead of knowing the real database's shape.
MEDICAL_DB_ENABLED = False
MEDICAL_DB_URL = None  # e.g. an internal API base URL, once it exists

# ---------------------------------------------------------------------------
# OCR (EasyOCR)
# ---------------------------------------------------------------------------
OCR_LANGUAGES = ["en", "ko"]
OCR_GPU = False  # EasyOCR's GPU path is CUDA-only; no effect on Apple Silicon, left False to avoid a misleading flag

# ---------------------------------------------------------------------------
# Vision-language model
# ---------------------------------------------------------------------------
# RE-BENCHMARKED on this Mac (2026-09-19, benchmark_task_types.py,
# 10 questions x English+Korean across color/object_id/scene_description/
# presence, transformers==4.49.0, MPS): the Windows-era conclusion
# ("smolvlm" fast, qwen2vl too slow) does NOT hold here.
#
#   qwen2vl:   1.88-2.57s/question (avg ~2.1s), ALL under 3s. Answered
#              Korean questions in Korean correctly and consistently.
#              Clear pick: fastest backend that is also accurate.
#   moondream: 5.72-6.05s/question, ALL over 3s. Also less accurate --
#              hallucinated "no object" on an image with an object in it,
#              and gave nonsense answers to Korean questions (English-only
#              language following). Disqualified on both speed and accuracy.
#   smolvlm:    GGUF via llama-cpp-python -- still blocked, llama-cpp-python
#               fails to build (Xcode command-line-tools license not yet
#               accepted on this Mac, blocking its CMake/clang build step).
#   smolvlm_hf: same SmolVLM2-500M weights, but the plain transformers-native
#               checkpoint (HuggingFaceTB/SmolVLM2-500M-Video-Instruct)
#               instead of the GGUF -- needs no compilation, so it sidesteps
#               the llama-cpp-python blocker entirely. Benchmarked
#               2026-09-19: 1.1-2.0s/question (avg ~1.4s) on MPS, ALL under
#               3s, and clearly the fastest of the three backends actually
#               tested on this machine so far -- but see the accuracy
#               caveats logged in benchmark_results_smolvlm_hf.csv (weaker
#               than qwen2vl on a couple of the Korean/object-naming cases;
#               same known failure class documented in README's "which VLM"
#               section). Once llama-cpp-python builds, compare the GGUF
#               version too -- quantization could change speed/accuracy
#               slightly from this transformers-native measurement.
#
# Metal (via MPS) is why qwen2vl beats its Windows numbers so drastically:
# the vision-encoder pass that made it 30-40s/question on the CPU-only
# Windows laptop is fast here.
VLM_BACKEND = "qwen2vl"

# GGUF weights, run via llama-cpp-python + its multimodal chat handler --
# NOT plain transformers/PyTorch. This is what got a ~2B-class model down
# from 30-40s to single digits on the CPU-only Windows laptop; keep it this
# way even though Metal is available, since llama.cpp's quantized path is
# still the fast option here. Currently unbuildable on this machine, see note above.
SMOLVLM_REPO = "ggml-org/SmolVLM2-500M-Video-Instruct-GGUF"
SMOLVLM_MODEL_FILE = "SmolVLM2-500M-Video-Instruct-Q8_0.gguf"
SMOLVLM_MMPROJ_FILE = "mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf"
SMOLVLM_N_CTX = 2048
SMOLVLM_MAX_TOKENS = 128
SMOLVLM_GPU_LAYERS = -1  # -1 = offload every layer; llama.cpp auto-detects Metal on Apple Silicon, no extra config needed

# Same SmolVLM2-500M weights as above, transformers-native (no llama.cpp/GGUF).
SMOLVLM_HF_MODEL_ID = "HuggingFaceTB/SmolVLM2-500M-Video-Instruct"

# Fallback backends (plain transformers), switch VLM_BACKEND to use these.
QWEN2VL_MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct"
# Newer generation, being evaluated (2026-09-19) for "much better accuracy,
# even if slower" -- 3B is the realistic pick given the 4s round-trip
# ceiling (7B is ~3.5x the params of qwen2vl above and OOM'd at fp32 in an
# isolated test; may still be worth trying through the real fp16 pipeline
# path since that roughly halves memory, but 3B is the safer bet).
QWEN25VL_MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
MOONDREAM_MODEL_ID = "vikhyatk/moondream2"
TRANSFORMERS_DEVICE = "mps"  # Apple Silicon GPU via PyTorch MPS backend; falls back to "cpu" automatically if unavailable
# MPS mixed-precision conv kernels choke on fp16 pixel_values vs fp16
# weights mismatch for some vision towers (seen on smolvlm_hf); float32 is
# the safe default across all three transformers backends on this hardware.
TRANSFORMERS_DTYPE = "float32"

# CRITICAL FIX FOUND ON REAL HARDWARE (2026-09-19): main.ask_vlm() resizes
# every frame to this size before inference. Feeding Qwen2-VL the raw
# camera frame (CAMERA_WIDTH x CAMERA_HEIGHT, 1280x720) measured 9.19s per
# question -- the vision-encoder cost scales with resolution, and earlier
# isolated benchmarks never caught this because they used small synthetic
# 800x400 test images, not real camera-resolution frames. Resizing the
# SAME real frame down to 640x360 before inference cut that to 1.88s with
# an identical answer in that test. Set to None to disable resizing (use
# the raw camera frame) if accuracy ever seems to suffer -- but that was
# the whole point of chasing this bug, so verify first with a real
# before/after comparison before reverting it.
VLM_INPUT_WIDTH = 640
VLM_INPUT_HEIGHT = 360

VLM_MAX_NEW_TOKENS = 128
# CONFIRMED on real hardware (2026-09-20): without an explicit instruction,
# Qwen2-VL answered Korean questions in Korean MOST of the time but not
# always (one observed case answered in English to a Korean question) --
# language-matching wasn't reliably happening on its own. Added an
# explicit instruction rather than relying on the model to infer it.
VLM_PROMPT_TEMPLATE = (
    "You are a visual assistant for a blind or low-vision user. Answer the "
    "question in one or two short, direct sentences based only on what is "
    "visible in the image. Always answer in the SAME language the question "
    "was asked in -- if the question is in Korean, answer in Korean; if "
    "in English, answer in English. Question: {question}"
)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
LOG_LEVEL = "INFO"
