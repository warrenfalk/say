"""Own the persistent model worker and the one playback process."""

import asyncio
import base64
import contextlib
import os

from .common import MAX_MESSAGE, command, decode, encode, python_environment


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

    async def speak(self, text: str, first: bool):
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
                    "-",
                    stdin=asyncio.subprocess.PIPE,
                )
            self.player.stdin.write(base64.b64decode(event["pcm"], validate=True))
            await self.player.stdin.drain()

    async def finish(self):
        if self.player is None:
            return
        self.player.stdin.close()
        code = await self.player.wait()
        self.player = None
        if code:
            raise RuntimeError(f"Audio playback failed (exit {code}); check the PipeWire output")
