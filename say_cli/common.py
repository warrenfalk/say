"""Small shared helpers; the client and queue never import the model runtime."""

import json
import os
import stat
import sys
from pathlib import Path

MAX_MESSAGE = 64 * 1024
TEXT_BLOCK = 4096
IDLE_SECONDS = 30 * 60


def runtime_dir() -> Path:
    if override := os.environ.get("SAY_RUNTIME_DIR"):
        path = Path(override)
    elif base := os.environ.get("XDG_RUNTIME_DIR"):
        path = Path(base) / "say"
    else:
        path = Path("/tmp") / f"say-{os.getuid()}"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"Unsafe runtime directory: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f"Runtime directory must have mode 0700: {path}")
    return path


def command(module: str) -> list[str]:
    return [sys.executable, "-m", f"say_cli.{module}"]


def python_environment() -> dict[str, str]:
    # Nix's entry points extend sys.path inside Python. Child interpreters need
    # those same package paths when launched outside the source checkout.
    return {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}


def encode(message: dict) -> bytes:
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> dict:
    if not line or len(line) > MAX_MESSAGE:
        raise ValueError("Invalid or oversized message")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")  # noqa: TRY004 -- Invalid wire format.
    return message
