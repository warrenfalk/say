{
  description = "Queued, streaming local speech with Pocket TTS";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.cudaSupport = false;
        # The official CPU wheel bundles Intel's redistributable math libraries.
        config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "torch";
      };
      python = pkgs.python313.override {
        packageOverrides = final: prev: {
          torch = final.callPackage ./nix/torch-cpu.nix { };
          sympy = prev.sympy.overridePythonAttrs {
            version = "1.13.1";
            src = pkgs.fetchPypi {
              pname = "sympy";
              version = "1.13.1";
              hash = "sha256-nOv34E/xYgFc4xycbJFE2qNKk70IL1T9jxLeyk9HUV8=";
            };
          };
          # Pocket imports yaml but omits it from its declared dependencies.
          pocket-tts = prev.pocket-tts.overridePythonAttrs (old: {
            dependencies = old.dependencies ++ [ final.pyyaml ];
          });
        };
      };
      models = import ./nix/models.nix { inherit pkgs; };
      say = python.pkgs.buildPythonApplication {
        pname = "say-local";
        version = "0.1.0";
        pyproject = true;
        src = pkgs.lib.fileset.toSource {
          root = ./.;
          fileset = pkgs.lib.fileset.unions [
            ./pyproject.toml ./README.md ./THIRD_PARTY.md
            (pkgs.lib.fileset.fileFilter (file: file.hasExt "py") ./say_cli)
            (pkgs.lib.fileset.fileFilter (file: file.hasExt "py") ./tests)
          ];
        };
        build-system = [ python.pkgs.setuptools ];
        dependencies = [ python.pkgs.pocket-tts python.pkgs.tomlkit ];
        nativeCheckInputs = [ pkgs.ruff pkgs.pipewire pkgs.wireplumber pkgs.dbus ];
        checkPhase = ''
          runHook preCheck
          python -m unittest discover -s tests -v
          ruff check say_cli tests
          ruff format --check say_cli tests
          python tests/check_package.py "$out"
          dbus-run-session --config-file=${pkgs.dbus}/share/dbus-1/session.conf -- \
            python -m unittest discover -s tests -p check_pipewire.py -v
          runHook postCheck
        '';
        postInstall = ''
          install -Dm644 README.md "$out/share/doc/say/README.md"
          install -Dm644 THIRD_PARTY.md "$out/share/doc/say/THIRD_PARTY.md"
        '';
        makeWrapperArgs = [
          "--set SAY_MODEL_DIR ${models}"
          "--set SAY_PLAYER ${pkgs.pipewire}/bin/pw-cat"
          "--set SAY_PW_DUMP ${pkgs.pipewire}/bin/pw-dump"
          "--set HF_HUB_OFFLINE 1"
          "--set HF_HUB_DISABLE_TELEMETRY 1"
          "--set DO_NOT_TRACK 1"
          "--set OMP_NUM_THREADS 1"
          "--set OPENBLAS_NUM_THREADS 1"
        ];
        pythonImportsCheck = [ "say_cli" ];
        meta = {
          description = "Speak text locally with queued playback and streaming input";
          mainProgram = "say";
          platforms = [ system ];
        };
      };
    in {
      packages.${system} = { default = say; inherit say models; };
      apps.${system}.default = {
        type = "app";
        program = "${say}/bin/say";
        meta.description = say.meta.description;
      };
      checks.${system}.default = say;
      devShells.${system}.default = pkgs.mkShell {
        packages = [ (python.withPackages (p: [ p.pocket-tts p.setuptools p.tomlkit ])) pkgs.pipewire pkgs.wireplumber pkgs.dbus pkgs.ruff ];
        SAY_MODEL_DIR = "${models}";
        SAY_PLAYER = "${pkgs.pipewire}/bin/pw-cat";
        SAY_PW_DUMP = "${pkgs.pipewire}/bin/pw-dump";
        HF_HUB_OFFLINE = "1";
        HF_HUB_DISABLE_TELEMETRY = "1";
        DO_NOT_TRACK = "1";
        OMP_NUM_THREADS = "1";
        OPENBLAS_NUM_THREADS = "1";
      };
    };
}
