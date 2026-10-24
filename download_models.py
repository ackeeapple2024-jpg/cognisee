"""Pre-download every model the CURRENT config.py uses, so the first live
run works offline and doesn't stall on multi-GB downloads mid-question.
Called by setup.sh; safe to re-run (already-cached models are skipped).

Only fetches what config selects (VLM_BACKEND, WHISPER_MODEL_SIZE,
OCR_LANGUAGES) -- not every backend main.py knows about.
"""

import config

VLM_MODEL_IDS = {
    "qwen2vl": "QWEN2VL_MODEL_ID",
    "qwen25vl": "QWEN25VL_MODEL_ID",
    "moondream": "MOONDREAM_MODEL_ID",
}


def main() -> None:
    from huggingface_hub import snapshot_download

    attr = VLM_MODEL_IDS.get(config.VLM_BACKEND)
    if attr is None:
        print(f"VLM_BACKEND={config.VLM_BACKEND!r}: no pre-download rule here, it will download on first use.")
    else:
        model_id = getattr(config, attr)
        print(f"[1/3] VLM {model_id}")
        snapshot_download(model_id)

    print(f"[2/3] Whisper '{config.WHISPER_MODEL_SIZE}'")
    from faster_whisper import WhisperModel
    WhisperModel(config.WHISPER_MODEL_SIZE, device=config.WHISPER_DEVICE,
                 compute_type=config.WHISPER_COMPUTE_TYPE)

    print(f"[3/3] EasyOCR {config.OCR_LANGUAGES}")
    import easyocr
    easyocr.Reader(config.OCR_LANGUAGES, gpu=config.OCR_GPU)

    print("All models cached.")


if __name__ == "__main__":
    main()
