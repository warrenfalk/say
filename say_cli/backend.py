"""Own the persistent model worker and the one playback process."""

import asyncio
import base64
import contextlib
import json
import os

from .common import MAX_MESSAGE, command, decode, encode, python_environment
from .outputs import find_output


async def terminate(process):
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class Pocket:
    def __init__(self):
        self.worker = None
        self.player = None
        self.sample_rate = None
        self.output = None
        self.target = None

    @property
    def loaded(self):
        return self.worker is not None

    async def close(self):
        await terminate(self.player)
        self.player = None
        await terminate(self.worker)
        self.worker = None

    async def event(self):
        line = await self.worker.stdout.readline()
        if not line:
            raise RuntimeError("Pocket worker stopped unexpectedly")
        event = decode(line)
        if event.get("type") == "error":
            raise RuntimeError(event.get("message", "Pocket synthesis failed"))
        return event

    async def load(self):
        if self.worker is not None and self.worker.returncode is None:
            return
        self.worker = await asyncio.create_subprocess_exec(
            *command("worker"),
            env=python_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=MAX_MESSAGE,
        )
        ready = await self.event()
        if ready.get("type") != "ready":
            raise RuntimeError("Pocket worker did not become ready")
        self.sample_rate = ready["sample_rate"]

    async def speak(self, text: str, first: bool, output: str | None = None):
        if first:
            self.output = output
            # Resolve the saved name when its queue turn arrives. Target the
            # specific node instance, even if it disappears during model load.
            self.target = str((await find_output(output)).serial) if output is not None else None
        await self.load()
        self.worker.stdin.write(encode({"text": text, "reset_seed": first}))
        await self.worker.stdin.drain()
        while True:
            event = await self.event()
            if event.get("type") == "done":
                return
            if event.get("type") != "audio":
                raise RuntimeError("Unexpected Pocket worker message")
            if self.player is None:
                routing = []
                if self.target is not None:
                    routing = [
                        "--target",
                        self.target,
                        "--properties",
                        json.dumps(
                            {
                                "node.dont-fallback": True,
                                "node.dont-reconnect": True,
                                "node.dont-move": True,
                            }
                        ),
                    ]
                self.player = await asyncio.create_subprocess_exec(
                    os.environ.get("SAY_PLAYER", "pw-cat"),
                    "--playback",
                    "--raw",
                    "--rate",
                    str(self.sample_rate),
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "--media-role",
                    "Notification",
                    *routing,
                    "-",
                    stdin=asyncio.subprocess.PIPE,
                )
            try:
                self.player.stdin.write(base64.b64decode(event["pcm"], validate=True))
                await self.player.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise RuntimeError(self.playback_error()) from error

    def playback_error(self):
        if self.output is not None:
            return f"Audio playback failed on {self.output!r}; the output may have disconnected"
        return "Audio playback failed; check the PipeWire output"

    async def finish(self):
        if self.player is None:
            return
        self.player.stdin.close()
        code = await self.player.wait()
        self.player = None
        if code:
            raise RuntimeError(f"{self.playback_error()} (exit {code})")
