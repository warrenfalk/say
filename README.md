# say

Speak text locally with Pocket TTS and Charles's voice.

```sh
say Hello World
say 'Hello World!'
say -- --this-is-spoken-literally
printf 'Hello World!\n' | say
some-command | say
```

`say` waits until playback finishes. `--` ends option parsing; options after the
first text argument are also spoken literally. Options must precede speech text.
No arguments reads UTF-8 stdin; no arguments at a terminal
prints usage. Empty input succeeds silently. Errors go to stderr; successful
speech writes nothing to stdout.

## Audio output

```sh
say --choose
say --list-outputs
say --output NAME 'Hello World!'
say --output default 'Use the system default this time.'
some-command | say --output NAME
```

`--choose` shows a numbered terminal menu with friendly output descriptions and
marks the saved selection. Choose **System default** to follow normal system
routing again. Enter, `q`, EOF, or Ctrl+C cancels without changing preferences.
`--list-outputs` shows descriptions and the names accepted by `--output`, including
virtual outputs. These commands do not start the speech service or load the model.

The preference is stored in `$XDG_CONFIG_HOME/say/config.toml`, normally
`~/.config/say/config.toml`:

```toml
output = "alsa_output.example.analog-stereo"
```

An explicit `--output` takes precedence over the saved preference. With neither,
speech uses the system default. `--output default` bypasses the saved preference
for one invocation. The picker saves atomically and preserves existing comments
and other settings. This preference applies to `say` and is local to the user.

The client reads preferences for each invocation. Each queued request retains its
selection through all streamed phrases; changing the preference needs no service
restart or model reload. A named output is resolved when its queue turn arrives.
An unavailable or disconnected selected output produces an error, without falling
back to another device. Use `--choose` to select another output, or explicitly
request `--output default`.

After upgrading an older running service to this version, restart it once as
described below. The client checks the service's routing support before sending
text, so an old service cannot silently ignore an output selection.

## Speech and streaming

The fixed defaults are Pocket TTS 3.1.0, the April 2026 English model, Charles,
7 sampler steps, and a maximum of 180 text tokens per model chunk. Other settings
match the listening-room demo: temperature 0.3, seed 42 per request, no INT8
quantization or noise clamp, EOS threshold -4, and automatic trailing frames.

Audio plays through the selected PipeWire output as it is generated. Piped
text begins speaking before EOF: sentence endings and newlines release text, and
a 750 ms pause releases an unfinished phrase. Pending text is also bounded at
1,200 characters; Pocket applies its 180-token limit within each submitted
segment. Early phrase boundaries can change delivery compared with passing the
whole text as an argument. A producer that buffers its own output must flush it
before `say` can receive it.

All invocations by the same user share a first-in, first-out queue. A request
enters the queue when its first nonblank text arrives. An active pipe keeps its
place until it closes and its audio finishes, including pauses between phrases.
Different requests never play simultaneously. Up to 64 requests can wait.

Ctrl+C cancels only that invocation, including when it is queued. Cancelling
active speech terminates its player and Pocket worker; the next request reloads
Pocket. This is necessary because the upstream model starts generation threads
without a cooperative cancellation API. Queued cancellation preserves the loaded
model and the other requests.

## Service lifetime

The first command automatically starts a lightweight per-user background service.
It listens on a private Unix socket, loads Pocket on demand, and releases the
entire model worker after **30 minutes with no active or queued speech**. The
lightweight service remains available. Process shutdown releases PyTorch's
allocator caches as well as the weights.

Measured on this workstation on 2026-09-20, the initial PyTorch 2.13 worker occupied
941 MiB RSS after speaking (736 MiB private anonymous memory); the queue service
used 25 MiB RSS. The worker used no measurable CPU during a three-second idle
sample. The existing listening-room worker used about 1 GiB RSS and also consumed
no CPU during a ten-second idle sample. Memory can grow with longer passages.

The stock loader copies checkpoint tensors into the model, including conversion
from the checkpoint's mixed precision to float32. Retaining the original mmap
tensors with `assign=True` failed a local experiment due to the different dtypes.
A mapped loader would need converted weights and still retain Python/PyTorch and
synthesis caches in RAM. The initial implementation therefore uses the stock
loader and the idle timeout.

The service socket, PID lock and diagnostics are in `$XDG_RUNTIME_DIR/say`, falling
back to `/tmp/say-UID`. The directory is private to the user. Speech text and audio
are not recorded. Large pending inputs spill into anonymous temporary storage
instead of growing RAM usage without bound, and are discarded after the request.

To run the service in the foreground for diagnostics:

```sh
./result/bin/say-service
```

Only one service can own the queue, including when several commands start
simultaneously. Stop it with `kill -TERM "$(cat "$XDG_RUNTIME_DIR/say/service.lock")"`;
the next command starts it again. Stopping it also stops its model and playback
children. Restart the service after updating the package to use the new version.

## Nix

The flake currently supports `x86_64-linux`. PipeWire must be running for the user.
Nix supplies Python, Pocket, the audio player, and the official PyTorch 2.6.0 CPU
release evaluated in the listening-room demo. The English model, tokenizer and
Charles embedding are pinned by revision and SHA-256
and downloaded during the Nix build. Installed speech works offline without an
account, a first-use download, or a dependency on `~/source/local-tts`.

From this checkout (the `path:` form also works before the first commit):

```sh
nix build path:.
./result/bin/say Hello World
nix run path:. -- 'Hello World!'
```

After publication, add the repository as an input to the workstation flake and
include `inputs.say.packages.${pkgs.stdenv.hostPlatform.system}.default` in the
user's packages. The automatic service needs no separate activation unit.

## Development

`.envrc` uses the flake. Without direnv:

```sh
nix develop path:. -c python -m say_cli.client Hello World
nix develop path:. -c python -m unittest discover -s tests -v
nix develop path:. -c ruff check .
nix develop path:. -c ruff format --check .
nix flake check path:.
```

The tests cover FIFO playback, per-request outputs, config precedence and saving,
picker cancellation, incremental stdin, UTF-8, cancellation, idle unloading,
failure recovery and empty input without making audible speech.
The Nix build runs these checks and verifies simultaneous service autostarts from
outside the checkout, including recovery of a stale socket. It also starts a
private PipeWire graph with virtual outputs to check routing and disconnection
without touching audio hardware. Run that check separately with:

```sh
nix develop path:. -c dbus-run-session -- python -m unittest discover -s tests -p check_pipewire.py -v
```

Live model and physical playback checks are separate.

`SAY_RUNTIME_DIR` selects an isolated private runtime directory for development.
`SAY_MODEL_DIR`, `SAY_PLAYER`, and `SAY_PW_DUMP` are supplied by the flake; the installed wrappers
pin them to their Nix store paths. These are integration settings, not synthesis
tuning options. Only the worker imports Pocket/PyTorch. TOMLKit preserves comments
and formatting when the CLI saves preferences; audio discovery uses `pw-dump`.

Before updating the inference runtime, check that synthesis keeps ahead of playback:

```sh
nix develop path:. -c python -m say_cli.benchmark
```

This generates the short `Hello World!` example and a longer passage without
playing audio. `realtime_speed` must be above 1 for sustained streaming. The
`required_extra_buffer_seconds` metric reports the minimum additional startup
delay needed to avoid exhausting the generated audio in that run. Model startup
is excluded from these measurements.

The initial Nixpkgs PyTorch 2.13 build generated 1.2 seconds of `Hello World!`
in 2.07 seconds and the longer passage at about 0.63 times real time on this
workstation. The demo's official PyTorch 2.6.0 CPU wheel generated the same short
example in 0.97 seconds and the passage at 1.41 times real time, with the same
voice, sampler steps, token limit and seed. The flake pins that CPU wheel to
avoid the regular playback underruns observed with the initial runtime.
The rebuilt Nix package measured 0.96 seconds for the short example and 1.40 times
real time for the passage. Three actual `Hello World!` playback runs reported no
PipeWire errors, compared with counters reaching 224 in the original package.

See [THIRD_PARTY.md](THIRD_PARTY.md) for model and voice credits.
