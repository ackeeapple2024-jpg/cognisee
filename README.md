# CogniSee

An AI-glasses assistant for a visually impaired user: press a button, ask a
question out loud, get a spoken answer based on what a camera currently
sees. Runs 100% locally/offline -- no cloud calls, no wake word, no
always-on listening.

> **Provenance note:** this codebase was reconstructed on an M1 Pro
> MacBook (2026-09-19) from a written spec of a version that was built and
> extensively tested on a Windows laptop (LG Gram, i5-1035G7, no GPU). The
> original Windows source was not available on this machine, so this is a
> fresh implementation matching that design, not a copy of the exact code
> that was benchmarked. Anything below marked **VERIFY** has not yet been
> confirmed against real hardware on this Mac.

## Quickstart (new Mac, same hardware)

**Hardware this is set up for** -- with exactly these, no config changes
are needed:

| Part | What | How CogniSee finds it |
|---|---|---|
| Computer | Apple Silicon Mac (M1 or newer), 16 GB RAM recommended | -- |
| Camera | Small UVC endoscope camera, 640x480 native (macOS lists it as "HD camera", USB VendorID 13468 / ProductID 1041), plugged in **directly**, not through a shared hub | Native resolution 640x480 (`CAMERA_NATIVE_*` in `config.py`) |
| Mic + audio out | AirPods Pro (any AirPods should work) | Input device name contains "AirPods" (`MIC_NAME_HINT`) |
| Button | YOITCH Snapshot 2 Bluetooth clicker | Volume up/down media keys (`BUTTON_MEDIA_KEY_CODES`) |

Different hardware: change the matching `config.py` setting above, and use
the `test_*.py` scripts to find the right values.

**Steps:**

1. Install Python 3.10 from [python.org](https://www.python.org/downloads/)
   (the macOS installer).
2. In this folder: `./setup.sh` -- creates `venv/`, installs the exact
   tested package versions (`requirements.lock`), downloads the models
   (~5 GB, one time).
3. Plug in the camera, connect the AirPods (in your ears) and the clicker.
4. `./venv/bin/python doctor.py` -- checks every part and tells you what to
   fix. Expect macOS permission prompts the first time; grant them to
   Terminal: **Camera**, **Microphone**, **Input Monitoring** and
   **Accessibility** (System Settings -> Privacy & Security). After
   granting Input Monitoring/Accessibility, quit and reopen Terminal.
5. `./run.sh` -- wait for "CogniSee ready.", then press the clicker, ask,
   press again.

Only one CogniSee can run at a time (it refuses to start a second copy and
tells you the pid of the first). Stop it with Ctrl+C.

**Sharing this folder:** `./package.sh` makes `cognisee.zip` next to this
folder, without `venv/`, caches or personal session files -- the recipient
runs `./setup.sh` to rebuild those for their Mac.

## Core loop

Button press (press-to-talk: press once to start recording, press again to
stop) -> record audio + grab a camera frame -> transcribe the question
(Whisper) -> route the question to either OCR or a vision-language model ->
speak the answer (TTS) -> repeat.

## Files

| File | Purpose |
|---|---|
| `setup.sh` | One-time install: venv, exact package versions, model downloads |
| `run.sh` | Start the live pipeline |
| `doctor.py` | Pre-flight check of hardware, permissions and models |
| `download_models.py` | Pre-downloads the models `config.py` uses (called by `setup.sh`) |
| `package.sh` | Zips this folder for sharing |
| `requirements.lock` | Exact package versions verified live (`requirements.txt` has the ranges + reasons) |
| `config.py` | All tunable settings, one place |
| `main.py` | Core pipeline: capture, STT, VLM, OCR, TTS, button handling, live loop |
| `test_camera.py` | Identify/verify the correct camera index |
| `test_mic.py` | Identify/verify the correct microphone device |
| `test_tts.py` | Verify audio output + TTS backend |
| `test_button.py` | Identify how the trigger clicker behaves on this OS |
| `test_pipeline_sample.py` | End-to-end routing + OCR/VLM test against saved sample images (all 5 cases, no live hardware) |
| `benchmark_task_types.py` | Latency/accuracy comparison across VLM backends (qwen2vl/moondream/smolvlm) -- reference for how `config.VLM_BACKEND` was chosen, rerun if reconsidering it |
| `samples/generate_samples.py` | Generates the synthetic test images used by both scripts above |

## Setup (macOS / Apple Silicon)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Unlike the Windows setup, `llama-cpp-python` ships prebuilt PyPI wheels for
Apple Silicon with Metal support already built in -- a plain `pip install`
is enough here. (The Windows README for this project needed a
`--extra-index-url` workaround to get a working wheel there; that does not
apply on macOS and should be treated as Windows-only guidance.)

## Hardware setup order

Run these **in order** -- each one identifies or verifies one piece of
hardware before the next step depends on it. Do not skip straight to
`main.py`.

1. **Camera identification needed real debugging, not just `--list`.**
   This laptop has 2 iPhones available as Continuity Camera sources, which
   enumerate exactly like a normal webcam AND whose index position shifts
   between runs based on the phone's proximity/wake state -- worse, it
   turned out OpenCV's AVFoundation backend doesn't even use the same
   device ordering as querying AVFoundation directly, so no form of
   "device at position N" reasoning is trustworthy here, same-process or
   not. What actually works: `main.resolve_camera_index()` finds the
   endoscope camera by **native resolution fingerprint** -- it's 640x480
   native, while the built-in FaceTime HD camera and every iPhone report
   1920x1080, so that resolution uniquely identifies the right physical
   device regardless of what index it lands on. (The original Sonix
   webcam, retired 2026-09-23, was fingerprinted the same way at
   1280x800.) `config.CAMERA_INDEX` is
   only a last-resort fallback now, not the source of truth -- see the
   long comment above it and above `resolve_camera_index()` in `main.py`
   for the two false starts that led here, worth reading before "fixing"
   this again. Needs Camera permission granted to Terminal/python -- if
   OpenCV logs "not authorized"/"access denied", run
   `tccutil reset Camera` and retry to get a fresh permission prompt.

2. **`python test_mic.py --list`**, then `--record <index>` to verify
   signal. The mic is found by name (`config.MIC_NAME_HINT = "AirPods"`
   since 2026-10-18, when the original AB13X USB mic stopped being
   available), not by index -- see "Already resolved" below for why the
   AirPods mic stream is kept open all session.

3. **`python test_tts.py`** to confirm audio output + the TTS backend
   work. Set your intended output (wired earphones, or check whether this
   MacBook model even has a headphone jack -- some are USB-C only) as the
   system default output first.

4. **`python test_button.py`** to confirm the trigger button. Already
   resolved for the YOITCH Snapshot 2 -- see "Trigger button" below --
   `config.BUTTON_BACKEND = "media_key_tap"` is the working default; this
   step is just confirming it still works on your setup (grant Input
   Monitoring permission if prompted).

5. **`python test_pipeline_sample.py`** (needs
   `python samples/generate_samples.py` run once first) to confirm STT +
   OCR + VLM all work end-to-end on saved images, no live hardware
   required. Already passing 16/16 as of the last run.

6. **`python benchmark_task_types.py`** to re-compare VLM backend speed on
   this machine if reconsidering `config.VLM_BACKEND` -- see "Which VLM"
   below for the already-completed comparison.

7. **`./venv/bin/python doctor.py`**, then **`./run.sh`** for the full
   live loop -- see Quickstart at the top.

## Trigger button (CONFIRMED, tested on real hardware 2026-09-19)

Same failure mode as Windows, just observable instead of hidden: the
YOITCH Snapshot 2 clicker doesn't send a normal key event on macOS either.
It sends real macOS system-defined **volume-key** events -- confirmed via
`test_button.py --probe-media-keys` to fire **both** `NX_KEYTYPE_SOUND_UP`
(code 0) and `NX_KEYTYPE_SOUND_DOWN` (code 1), which is why a plain
`pynput` hook (`test_button.py --probe`) never sees anything even though
the system volume audibly/visibly changes.

**Fix:** `config.BUTTON_BACKEND = "media_key_tap"` (default) --
`main.MediaKeyTapButtonListener` intercepts these events via a low-level
`CGEventTap` and **suppresses** them before macOS acts on them, instead of
just reacting after the volume has already changed
(`main.CoreAudioVolumeWatchListener`, the polling-based approach, is kept
only for reference -- it can detect a change but can't prevent it).
Two more things the real hardware forced:

- **Double-fire:** each physical click was observed firing ~2 key-press
  transitions in rapid succession (a HID quirk of this specific clicker).
  `config.BUTTON_DEBOUNCE_SEC = 0.4` collapses these into one logical
  press so a single click doesn't double-toggle recording.
- **Tap timeout:** macOS auto-disables a `CGEventTap` if its callback
  blocks too long. The callback dispatches `on_press()` on its own thread
  rather than calling it inline -- without this, real-pipeline `on_press()`
  work (opening the camera) reliably triggered "disabled by timeout" and
  risked dropping the next click.

**Known limitation -- read before leaving this running unattended:** the
tap is system-wide. While it's active, it intercepts *every* source of
these key codes, not just this clicker -- **your Mac's own keyboard volume
keys are redirected to the mic toggle too**, for as long as the process
runs. This is expected for an assistive-device app actively in use, but if
you want the pipeline running continuously in the background while keeping
normal keyboard volume control, that needs device-specific filtering
(matching the clicker's Bluetooth vendor ID via a lower-level HID API,
e.g. IOHIDManager) -- not implemented, since it's a meaningfully larger
addition than what's here.

**Permission required:** the event tap needs "Input Monitoring" granted to
whatever process runs it (Terminal.app, or the `python3` binary) --
`CGEventTapCreate()` returns `None` and logs an error if it's missing;
grant it in System Settings -> Privacy & Security -> Input Monitoring.

Requires `pyobjc-framework-Quartz` + `pyobjc-framework-Cocoa` (in
`requirements.txt`), macOS-only.

## The dual-engine question routing

Every transcribed question is routed to **one** of two engines --
**Qwen2-VL-2B** or **EasyOCR** -- never both. The routing itself
(`main.classify_question()`) checks three phrase lists in priority order and
returns `"medical"`, `"ocr"`, or `"vlm"`; `"medical"` and `"ocr"` both call
into EasyOCR underneath, kept as separate routes only so the medical case
has its own call site for the future database sync (see below).

- **Public transit** ("read this", "which exit", "what line", "what bus",
  "when does the bus arrive", "which gate", etc. -- full list in
  `config.OCR_TRIGGER_PHRASES`) -> `main.ask_ocr()` -> **EasyOCR**. Verified
  end-to-end on this Mac (`test_pipeline_sample.py`) against a Korean subway
  sign, an English exit sign, and a bus schedule sign, in both languages:
  0.4-0.5s per question once the model's loaded, and every answer correct.
- **Medical records** (medication name, dosage, prescription, lab results,
  appointment, refill, etc. -- full list in `config.MEDICAL_TRIGGER_PHRASES`)
  -> `main.ask_medical_record()` -> same EasyOCR engine, but through its own
  function so a future database lookup can be added in exactly one place
  (`main._lookup_medical_record_db()`, currently a documented no-op --
  `config.MEDICAL_DB_ENABLED = False`). **Known limitation, worth reading
  before trusting this for real medication decisions:** EasyOCR made small
  misreads on the synthetic test labels used here -- `"500mg"` came back as
  `"5OOmg"` (letter O for zero), and a Korean drug name had a dropped
  character. Harmless for "which exit is this," genuinely risky for a
  dosage number. This is exactly the gap `MEDICAL_DB_ENABLED` is meant to
  close once there's a real database to verify OCR'd text against --
  **don't treat raw OCR output as authoritative for medication/dosage
  until that sync exists.**
- **Everything else** (color, object ID, scene description,
  presence/absence) -> `main.ask_vlm()` -> **Qwen2-VL-2B**
  (`config.VLM_BACKEND = "qwen2vl"`).

### Why this split exists

Real benchmarking on the Windows laptop found that general-purpose small
VLMs (Qwen2-VL-2B, Moondream2) took ~30-40 seconds per question on that
CPU-only machine regardless of quantization, because the image
vision-encoder pass dominates the time, not generation length. EasyOCR was
added as a fast path specifically because it answers the same *kind* of
question in ~2s using fundamentally different (non-LLM) tech.

Transit-navigation questions were folded into the OCR route (not just
literal "read this") after a synthetic Korean subway-sign test found that
even a fast small VLM (SmolVLM-500M v1) completely failed -- it answered
"Exit 3" to every question asked, regardless of what was actually asked or
visible.

## Which VLM, and why

Five models were benchmarked on the Windows laptop:

| Model | Speed | Accuracy | Verdict |
|---|---|---|---|
| Qwen2-VL-2B | ~30-40s/question | accurate | too slow |
| Moondream2 | ~34-38s/question | accurate-ish | too slow |
| SmolVLM-256M (GGUF) | ~5s | 0/4 test questions, hallucinated a person not in frame | disqualified |
| SmolVLM-500M v1 (GGUF) | ~5.5-6.5s | 7/8 correct on English; totally broken on the Korean sign test | disqualified for the Korean use case |
| **SmolVLM2-500M v2 (GGUF, current default)** | ~6-8s | similar English accuracy to v1, AND correctly answered from the Korean sign | **current default** |

`config.VLM_BACKEND = "smolvlm"` runs via `llama-cpp-python` +
`MtmdChatHandler` (see `_get_vlm()` / `_ask_smolvlm()` in `main.py`), **not**
plain transformers/PyTorch -- that GGUF+llama.cpp path with quantization is
what got a ~2B-class model down from 30-40s to single digits on CPU.
Qwen2-VL and Moondream2 remain switchable fallbacks
(`config.VLM_BACKEND = "qwen2vl"` or `"moondream"`, plain transformers) if
accuracy ever needs to trump speed.

### Re-benchmarked on this M1 Pro -- the Windows hierarchy does not hold

Run 2026-09-19 via `benchmark_task_types.py`: 10 questions (English +
Korean) across the 4 VLM task types (color, object ID, scene description,
presence/absence), each backend loaded once via MPS (Metal). Full
per-question results/answers in `benchmark_results_qwen_moondream.csv`.

| Backend | Latency (min-max, avg) | Under 3s? | Notes |
|---|---|---|---|
| **Qwen2-VL-2B** | 1.88-2.57s, avg ~2.1s | **10/10** | Correctly answered Korean questions in Korean; only minor misses were describing an abstract test shape as "a red object" instead of naming it "mug" |
| Moondream2 | 5.72-6.05s, avg ~5.8s | 0/10 | Slower AND less accurate: hallucinated "no table or object" on an image that clearly had one; answered Korean questions in English with unrelated content ("A triangle is visible") |
| SmolVLM2-500M | not yet re-tested on this machine | -- | `llama-cpp-python` failed to build -- Xcode command-line-tools license needs accepting (`sudo xcodebuild -license`) before its CMake/Metal build can run. Re-run `benchmark_task_types.py --backends smolvlm` once that's done. |

**`config.VLM_BACKEND` is now set to `"qwen2vl"`**, overriding the
Windows-era default of `"smolvlm"`. This is the opposite of what Windows
testing found (`qwen2vl` was disqualified there at 30-40s/question) --
Metal acceleration on Apple Silicon removes the vision-encoder bottleneck
that made the full 2B model too slow on the CPU-only Windows laptop, and
`qwen2vl`'s answers are also simply better than the smaller models' (it's
a full 2B instruction-tuned model vs. Moondream's ~2B differently-trained
or SmolVLM's 500M). Revisit this once SmolVLM2 numbers exist for this
machine -- if it's meaningfully faster AND comparably accurate, it may be
worth switching back for extra headroom, but don't assume that without
the numbers.

## Judgment calls made in this rebuild (not verified against the original)

Since the original Windows source wasn't available to port from, a few
implementation details were chosen fresh and may not match what was
actually tested there:

- **STT library:** `faster-whisper` (CTranslate2 backend), not
  `openai-whisper`. Same Whisper weights, meaningfully faster on both CPU
  and Apple Silicon. If the Windows build specifically used
  `openai-whisper` and behavior needs to match exactly, this should be
  swapped.
- **TTS library:** defaults to shelling out to macOS's `say` command
  (`config.TTS_BACKEND = "say"`) for natural system voices with zero extra
  dependencies. `pyttsx3` (cross-platform, `nsss` driver on macOS) is
  implemented as the alternative backend -- switch
  `config.TTS_BACKEND = "pyttsx3"` if the original used something closer
  to that.

## Historical: Sonix webcam stream drops (resolved by replacing the camera)

**Resolved 2026-09-23** by retiring the Sonix webcam for the current
endoscope camera on a direct connection, which showed zero drops in a
5-minute continuous-read test (293/293 reads) and in live sessions since.
Kept because the lessons apply to any USB camera: prefer a direct port
over a shared hub, and don't add reopen/retry loops without a clean
before/after comparison.

Extensively debugged on real hardware (2026-09-19). The Sonix webcam (shared USB-C dongle with the AB13X
mic) intermittently fails to produce a working video stream, on a
timescale of seconds, in a way that does NOT track with anything the
software controls:

- Camera **identification** is confirmed correct and NOT the issue --
  `resolve_camera_index()`'s resolution fingerprint reliably finds the
  right device by content whenever a device is findable at all (verified
  repeatedly, including a direct cover-the-lens A/B test).
- `system_profiler`/`ioreg` saying the device is present does NOT
  guarantee `cv2.VideoCapture` can actually open a working stream for it
  at that same moment -- these are different layers that can disagree.
- Failures correlate with idle gaps between uses (worked flawlessly for
  5+ consecutive questions asked back-to-back; failures showed up after
  longer pauses), consistent with the USB video stream idling/suspending
  and needing a moment to restart -- but a full reopen/rescan does not
  always successfully restart it within a few seconds.
- Tried and reverted: read-retry-before-reopen and startup-retry-with-
  backoff (`CAMERA_READ_RETRIES`/`CAMERA_PRIME_RETRIES` in an earlier
  version of `config.py`) made things WORSE, not better -- a run that
  worked flawlessly with the simple "open once, reopen once on failure"
  logic later started failing once that retry complexity was added,
  suggesting repeated reopen/rescan cycles may themselves destabilize this
  particular camera's driver session rather than recover it.
- Current behavior (reverted to, and confirmed working): `capture_frame()`
  tries the existing handle, reopens ONCE on failure, and if that also
  fails, `resolve_camera_index()` raises `CameraNotFoundError` rather than
  guessing -- the user hears "Sorry, I couldn't see anything," never a
  wrong-camera answer. This is accepted as the current tradeoff (per
  explicit user decision, not swapping hardware) -- if it recurs
  frequently, the two remaining untried options are: a different USB-C
  dongle/hub (video streams need more sustained bandwidth than audio or
  simple enumeration, which is why a shared hub is the leading suspect),
  or connecting the webcam through a non-hub path if its cable allows it.

## Not yet done

- Device-specific button filtering (only suppress THIS clicker, leave the
  Mac's own keyboard volume keys alone) -- see the caveat in "Trigger
  button" above. Would need IOHIDManager-level matching by Bluetooth vendor
  ID, not attempted.
- `MEDICAL_DB_ENABLED` integration -- `main.ask_medical_record()` currently
  always falls back to raw OCR; see the "dual-engine question routing"
  section's medical-record caveat about OCR misreads on dosage numbers.
- If no input matches `MIC_NAME_HINT`, `main.resolve_mic_device_index()`
  falls back to the system default input rather than failing loudly the
  way the camera does. That fallback silently picked the AirPods mic (with
  its ~2.5s startup silence) on 2026-10-18 -- consider making a missing
  mic an explicit error too.
- Camera image is soft at room distance (the endoscope camera is built
  for close focus); no measured accuracy comparison by distance yet.

## Already resolved (kept here so it's not re-litigated)

- **Camera:** endoscope camera found via `main.resolve_camera_index()`'s
  native-resolution fingerprint (640x480), NOT a fixed
  `config.CAMERA_INDEX` -- see setup step 1 above.
- **Mic:** AirPods, found via `main.resolve_mic_device_index()`'s name
  match (`MIC_NAME_HINT = "AirPods"`).
- **AirPods mic startup silence:** every freshly opened AirPods mic
  stream delivers ~2.5s of exact digital zeros, which ate the start of
  every question (measured 4/4 recordings, 2026-10-18). Fixed by
  `config.MIC_PERSISTENT_STREAM = True` -- the stream opens once at
  startup and stays open; the button still starts/stops each question.
  Verified live: 5/5 questions transcribed word-for-word, 2.0-3.3s to
  answer. Same fix also restored the start chime, which was being
  swallowed by the AirPods switching into headset mode
  (`TONE_LEADING_SILENCE_SEC` added as a backstop).
- **Whisper language misdetection:** short clips were detected as
  Polish/Norwegian and transcribed as gibberish in that language.
  `config.WHISPER_ALLOWED_LANGUAGES = ["en", "ko"]` forces a re-transcribe
  in the likelier allowed language.
- **Hidden duplicate pipeline:** a pipeline whose launching shell was
  killed kept running and kept reacting to the clicker. `main.py` now
  takes a single-instance lock (`.cognisee.lock`) and refuses to start a
  second copy.
- **Mic gain:** `config.MIC_AUTO_GAIN_ENABLED = True` peak-normalizes
  audio before transcription -- the AB13X's raw input level measured only
  ~20% of full scale even close to the mouth, which made Whisper's speech
  detector reject clearly-audible real speech as silence.
- **STT model:** `config.WHISPER_MODEL_SIZE = "base"` (down from `"small"`)
  -- cuts transcription time ~66% for a small accuracy tradeoff, chosen to
  help hit a ~2.5-3.2s total round-trip target. Verified with multiple
  real spoken questions, all transcribed correctly.
- **Tone/TTS output:** `config.TONE_SAMPLE_RATE` decoupled from the mic's
  `SAMPLE_RATE` (a 16kHz tone crashed on AirPods, which doesn't support
  that output rate) and `config.TTS_LEADING_SILENCE_MS` added (Bluetooth
  audio was clipping the first fraction of a second of spoken answers
  after the multi-second silent processing gap).
- **VLM input resolution:** `config.VLM_INPUT_WIDTH`/`VLM_INPUT_HEIGHT`
  (640x360) -- feeding Qwen2-VL the raw 1280x720 Sonix camera frame cost 9.19s
  per question; resizing first cut that to 1.88s with an identical answer.
- **Button:** `config.BUTTON_BACKEND = "media_key_tap"`, confirmed working
  on real hardware -- see "Trigger button" above.
- **VLM backend:** `config.VLM_BACKEND = "qwen2vl"`, re-benchmarked on
  Apple Silicon and confirmed faster AND more accurate than the
  Windows-era `smolvlm` default here -- see "Which VLM" above.
