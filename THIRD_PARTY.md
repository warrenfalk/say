# Model and voice credits

- Pocket TTS code: [Kyutai Labs, Pocket TTS](https://github.com/kyutai-labs/pocket-tts),
  version 3.1.0, MIT license.
- Model and tokenizer: [Kyutai's Pocket TTS without voice cloning](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning),
  April 2026 English model, revision `d29db7978e464fb90cb3359ee0c69a273b9142cc`,
  [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
- Charles: Kyutai's preset voice embedding, revision
  `e81d79e8194ad4c7ce879c87a4258ef20cbf2487`, derived from VCTK speaker `p254`,
  recording `p254_023_enhanced.wav`. Kyutai provides the enhanced source recording
  in its [voice collection](https://huggingface.co/kyutai/tts-voices#vctk).
  The [CSTR VCTK Corpus](https://datashare.ed.ac.uk/handle/10283/3443) is by
  Junichi Yamagishi, Christophe Veaux and Kirsten MacDonald (2019), licensed under
  [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

The model, tokenizer and embedding are fetched unchanged. `say` selects the preset
voice and synthesis settings; it does not redistribute a modified model.

The pinned [PyTorch CPU wheel](https://pytorch.org/) is version `2.6.0+cpu`.
PyTorch is BSD-3-Clause licensed; the wheel also bundles Intel math libraries
under the Intel Simplified Software License. The flake's unfree-package allowance
is restricted to this `torch` package. Its bundled license notices remain in the
installed wheel.
