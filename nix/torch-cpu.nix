{
  lib,
  stdenv,
  buildPythonPackage,
  fetchurl,
  autoPatchelfHook,
  filelock,
  fsspec,
  jinja2,
  networkx,
  numpy,
  setuptools,
  sympy,
  typing-extensions,
}:

# Match the CPU runtime measured in local-tts. The nixpkgs 2.13 build produced
# only about 0.63x real-time speech on this workstation with seven sampler steps.
buildPythonPackage {
  pname = "torch";
  version = "2.6.0+cpu";
  format = "wheel";
  src = fetchurl {
    url = "https://download.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp313-cp313-linux_x86_64.whl";
    hash = "sha256-5w7i43rSepAgHRAaQcLhDffPFanr0XwIT1TPJRjFe98=";
  };
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dependencies = [ filelock fsspec jinja2 networkx numpy setuptools sympy typing-extensions ];
  pythonImportsCheck = [ "torch" ];
  dontStrip = true;
  meta = {
    description = "Pinned PyTorch CPU runtime for Pocket TTS";
    homepage = "https://pytorch.org/";
    license = with lib.licenses; [ bsd3 issl ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
  };
}
