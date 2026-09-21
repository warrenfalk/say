"""FIFO speech service on a private Unix socket; no network listener."""

import asyncio
import contextlib
import fcntl
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .backend import Pocket
from .common import (
    IDLE_SECONDS,
    MAX_MESSAGE,
    PROTOCOL_VERSION,
    TEXT_BLOCK,
    decode,
    encode,
    runtime_dir,
)
from .config import validate_output
from .input import TextInput
from .outputs import OutputUnavailable
from .text import phrases


@dataclass(eq=False)
class Request:
    writer: asyncio.StreamWriter
    streaming: bool
    output: str | None = None
    parts: TextInput = field(default_factory=TextInput)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    task: asyncio.Task | None = None


class Service:
    def __init__(self, backend=None, idle_seconds=IDLE_SECONDS):
        self.backend = backend if backend is not None else Pocket()
        self.idle_seconds = idle_seconds
        self.queue = asyncio.Queue(maxsize=64)
        self.connections = set()
        self.consumer = None

    async def reply(self, writer, message):
        if not writer.is_closing():
            with contextlib.suppress(ConnectionError):
                writer.write(encode(message))
                await writer.drain()

    async def read_parts(self, reader, request):
        while True:
            message = decode(await reader.readline())
            if message.get("type") == "end":
                request.parts.put(None)
                # Keep the connection open through playback, so disconnection
                # always means cancellation, even after all input was sent.
                if await reader.read(1):
                    raise ValueError("Unexpected input after end")
                return
            text = message.get("text")
            if message.get("type") != "text" or not isinstance(text, str):
                raise ValueError("Expected text or end")
            if len(text) > TEXT_BLOCK:
                raise ValueError("Text block is too large")
            request.parts.put(text)

    async def handle(self, reader, writer):
        handler = asyncio.current_task()
        self.connections.add(handler)
        request = None
        receiver = None
        finished = None
        try:
            start = decode(await reader.readline())
            if start.get("type") == "hello":
                if start.get("protocol") != PROTOCOL_VERSION:
                    raise RuntimeError("Incompatible speech client; update say and say-service")
                await self.reply(writer, {"type": "ready", "protocol": PROTOCOL_VERSION})
                start = decode(await reader.readline())
            if start.get("type") != "start" or not isinstance(start.get("stream"), bool):
                raise ValueError("Invalid speech request")
            output = start.get("output")
            if output is not None:
                validate_output(output)
            # An open, silent stdin must not take over the speech queue.
            while True:
                message = decode(await reader.readline())
                if message.get("type") == "end":
                    await self.reply(writer, {"type": "done"})
                    return
                text = message.get("text")
                if message.get("type") != "text" or not isinstance(text, str):
                    raise ValueError("Expected text or end")
                if len(text) > TEXT_BLOCK:
                    raise ValueError("Text block is too large")
                if text.strip():
                    break
            request = Request(writer=writer, streaming=start["stream"], output=output)
            request.parts.put(text)
            try:
                self.queue.put_nowait(request)
            except asyncio.QueueFull:
                raise RuntimeError("Speech queue is full; try again after queued speech finishes")
            receiver = asyncio.create_task(self.read_parts(reader, request))
            finished = asyncio.create_task(request.done.wait())
            completed, _ = await asyncio.wait(
                [receiver, finished], return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in completed:
                await receiver
        except (OSError, ValueError, RuntimeError) as error:
            await self.reply(writer, {"type": "error", "message": str(error)})
        finally:
            if request is not None:
                request.cancelled = True
                if request.task is not None and not request.task.done():
                    request.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await request.task
                request.parts.close()
            for task in (receiver, finished):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, OSError, ValueError):
                        await task
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.connections.discard(handler)

    async def speak(self, request):
        first = True
        try:
            async for text in phrases(request.parts, request.streaming):
                await self.backend.speak(text, first, request.output)
                first = False
            await self.backend.finish()
            await self.reply(request.writer, {"type": "done"})
        except asyncio.CancelledError:
            # Pocket's generator has background threads without a cancellation
            # API. Stop its process so cancelled speech cannot leak into a job.
            await self.backend.close()
            raise
        except OutputUnavailable as error:
            # Selection is checked before starting synthesis or playback, so a
            # missing output does not discard an already loaded model.
            await self.reply(request.writer, {"type": "error", "message": str(error)})
        except Exception as error:  # noqa: BLE001 -- Report worker errors and keep the queue alive.
            await self.backend.close()
            await self.reply(request.writer, {"type": "error", "message": str(error)})
        finally:
            request.done.set()

    async def consume(self):
        while True:
            try:
                if self.backend.loaded:
                    request = await asyncio.wait_for(self.queue.get(), self.idle_seconds)
                else:
                    request = await self.queue.get()
            except TimeoutError:
                await self.backend.close()
                continue
            if request.cancelled:
                request.done.set()
                continue
            request.task = asyncio.create_task(self.speak(request))
            try:
                await request.task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise

    async def close(self):
        for task in list(self.connections):
            task.cancel()
        if self.connections:
            await asyncio.gather(*self.connections, return_exceptions=True)
        if self.consumer is not None:
            self.consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.consumer
        await self.backend.close()


async def serve(path: Path):
    service = Service()
    path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(service.handle, path=str(path), limit=MAX_MESSAGE)
    os.chmod(path, 0o600)
    service.consumer = asyncio.create_task(service.consume())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        await service.close()
        path.unlink(missing_ok=True)


def main():
    try:
        directory = runtime_dir()
        with (directory / "service.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            lock.seek(0)
            lock.truncate()
            lock.write(str(os.getpid()) + "\n")
            lock.flush()
            asyncio.run(serve(directory / "say.sock"))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"say-service: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
