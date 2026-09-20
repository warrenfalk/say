"""The only process that imports PyTorch. Exiting releases all model memory."""

import base64
import contextlib
import json
import os
import sys
from pathlib import Path

from .common import encode


def run(emit):
    import numpy as np
    import torch
    from pocket_tts import TTSModel
    from pocket_tts.utils.config import CONFIGS_DIR, load_config

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    assets = Path(os.environ["SAY_MODEL_DIR"])
    config = load_config(CONFIGS_DIR / "english_2026-04.yaml")
    config.weights_path = str(assets / "model.safetensors")
    config.weights_path_without_voice_cloning = None
    config.flow_lm.lookup_table.tokenizer_path = str(assets / "tokenizer.model")
    # Pocket's public loader only accepts a YAML filename. Its pinned constructor
    # takes the same config directly, keeping all artifact paths in the Nix store.
    model = TTSModel._from_pydantic_config_with_weights(
        config, temp=0.3, sampler_decode_steps=7, noise_clamp=None, eos_threshold=-4.0
    )
    voice = model.get_state_for_audio_prompt(assets / "charles.safetensors")
    emit({"type": "ready", "sample_rate": model.sample_rate})
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("reset_seed"):
            torch.manual_seed(42)
        with torch.no_grad():
            for audio in model.generate_audio_stream(voice, request["text"], max_tokens=180):
                samples = np.asarray(audio.detach().cpu(), dtype=np.float32).reshape(-1)
                if not np.isfinite(samples).all():
                    raise RuntimeError("Pocket produced invalid audio samples")
                pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                if pcm:
                    emit({"type": "audio", "pcm": base64.b64encode(pcm).decode("ascii")})
        emit({"type": "done"})


def main():
    protocol = sys.stdout.buffer

    def emit(message):
        protocol.write(encode(message))
        protocol.flush()

    try:
        with contextlib.redirect_stdout(sys.stderr):
            run(emit)
    except Exception as error:  # noqa: BLE001 -- Send initialization/inference errors over the protocol.
        emit({"type": "error", "message": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
