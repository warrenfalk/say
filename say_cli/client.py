"""Command-line parsing and streaming text to the per-user speech service."""

import argparse
import codecs
import contextlib
import fcntl
import os
import socket
import subprocess
import sys
import threading
import time

from . import __version__
from .common import TEXT_BLOCK, command, decode, encode, python_environment, runtime_dir


def parse_args(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(
        prog="say",
        description="Speak text locally. With no text arguments, read streaming UTF-8 from stdin.",
        epilog="Uses Pocket TTS, Charles, 7 sampler steps, and at most 180 text tokens per chunk.",
    )
    parser.add_argument("--version", action="version", version=f"say {__version__}")
    parser.add_argument(
        "text", nargs=argparse.REMAINDER, help="text to speak; use -- before leading -"
    )
    words = parser.parse_args(argv).text
    if words[:1] == ["--"]:
        words = words[1:]
    return words


def connect() -> socket.socket:
    directory = runtime_dir()
    path = str(directory / "say.sock")

    def attempt():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
        except OSError:
            sock.close()
            raise
        return sock

    try:
        return attempt()
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    # Serialize the check-and-spawn operation. The daemon's lifetime lock alone
    # allows a delayed duplicate launch to start after the winner shuts down.
    with (directory / "startup.lock").open("a") as startup:
        fcntl.flock(startup, fcntl.LOCK_EX)
        try:
            return attempt()
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        with (directory / "service.log").open("ab") as log:
            subprocess.Popen(
                command("service"),
                env=python_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return attempt()
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.025)
        raise RuntimeError(f"Speech service did not start; see {directory / 'service.log'}")


def send_input(sock: socket.socket, words: list[str], errors: list[Exception]):
    try:
        sock.sendall(encode({"type": "start", "stream": not words}))
        if words:
            text = " ".join(words)
            for offset in range(0, len(text), TEXT_BLOCK):
                sock.sendall(encode({"type": "text", "text": text[offset : offset + TEXT_BLOCK]}))
        else:
            decoder = codecs.getincrementaldecoder("utf-8")()
            while chunk := os.read(sys.stdin.fileno(), TEXT_BLOCK):
                if text := decoder.decode(chunk):
                    sock.sendall(encode({"type": "text", "text": text}))
            if text := decoder.decode(b"", final=True):
                sock.sendall(encode({"type": "text", "text": text}))
        sock.sendall(encode({"type": "end"}))
    except (OSError, UnicodeError) as error:
        errors.append(error)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def main() -> int:
    words = parse_args(sys.argv[1:])
    if not words and sys.stdin.isatty():
        print("Usage: say [--] TEXT ...  or  command | say", file=sys.stderr)
        return 2
    try:
        with connect() as sock:
            errors: list[Exception] = []
            sender = threading.Thread(target=send_input, args=(sock, words, errors), daemon=True)
            sender.start()
            try:
                with sock.makefile("rb") as replies:
                    line = replies.readline()
                if errors:
                    raise errors[0]
                if not line:
                    raise RuntimeError("Speech service disconnected before playback finished")
                reply = decode(line)
                if reply.get("type") != "done":
                    raise RuntimeError(reply.get("message", "Speech service returned an error"))
            finally:
                # Wake the service's disconnect monitor, including on Ctrl+C.
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"say: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
