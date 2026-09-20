{ pkgs }:
let
  base = "https://huggingface.co/kyutai/pocket-tts-without-voice-cloning/resolve";
  revision = "d29db7978e464fb90cb3359ee0c69a273b9142cc";
  model = pkgs.fetchurl {
    url = "${base}/${revision}/languages/english_2026-04/model.safetensors";
    hash = "sha256-vpxrSHbT8wdAqCJd/KouQ9xK63U8FScnNb7ha7tKuwo=";
  };
  tokenizer = pkgs.fetchurl {
    url = "${base}/${revision}/languages/english_2026-04/tokenizer.model";
    hash = "sha256-1GF2WuF5VmZ4yTCRxfpvKYTDG76ZC/GqYtksZNkbw/Y=";
  };
  voice = pkgs.fetchurl {
    url = "${base}/e81d79e8194ad4c7ce879c87a4258ef20cbf2487/languages/english_2026-04/embeddings/charles.safetensors";
    hash = "sha256-KZ7cIBgu7M+/lOMIYm8lnaT78znaqNWQUhjysXdGObg=";
  };
in pkgs.linkFarm "say-pocket-english-charles" [
  { name = "model.safetensors"; path = model; }
  { name = "tokenizer.model"; path = tokenizer; }
  { name = "charles.safetensors"; path = voice; }
]
