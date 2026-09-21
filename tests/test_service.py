import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path

from say_cli.common import decode, encode
from say_cli.outputs import OutputUnavailable
from say_cli.service import Service


class Backend:
    def __init__(self):
        self.loaded = False
        self.spoken = []
        self.destinations = []
        self.finishes = 0
        self.loads = 0
        self.unloads = 0
        self.gate = asyncio.Event()
        self.gate.set()

    async def speak(self, text, first, output=None):
        if output == "gone":
            raise OutputUnavailable("Audio output 'gone' is unavailable")
        if not self.loaded:
            self.loaded = True
            self.loads += 1
        self.spoken.append(text)
        self.destinations.append((text, output))
        if text == "fail":
            raise RuntimeError("test synthesis failure")

    async def finish(self):
        await self.gate.wait()
        self.finishes += 1

    async def close(self):
        if self.loaded:
            self.unloads += 1
        self.loaded = False


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "test.sock")
        self.backend = Backend()
        self.service = Service(backend=self.backend, idle_seconds=0.1)
        self.server = await asyncio.start_unix_server(self.service.handle, path=self.path)
        self.service.consumer = asyncio.create_task(self.service.consume())
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        await self.service.close()
        self.directory.cleanup()

    async def client(self, text=None, end=True, streaming=False, output=None):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.writers.append(writer)
        writer.write(encode({"type": "start", "stream": streaming, "output": output}))
        if text is not None:
            writer.write(encode({"type": "text", "text": text}))
        if end:
            writer.write(encode({"type": "end"}))
        await writer.drain()
        return reader, writer

    async def reply(self, reader):
        return decode(await asyncio.wait_for(reader.readline(), 3))

    async def test_fifo_waits_for_playback_to_finish(self):
        self.backend.gate.clear()
        first, _ = await self.client("first")
        await eventually(lambda: self.backend.spoken == ["first"])
        second, _ = await self.client("second")
        await eventually(lambda: self.service.queue.qsize() == 1)
        self.assertEqual(self.backend.spoken, ["first"])
        self.backend.gate.set()
        self.assertEqual(await self.reply(first), {"type": "done"})
        self.assertEqual(await self.reply(second), {"type": "done"})
        self.assertEqual(self.backend.spoken, ["first", "second"])
        self.assertEqual(self.backend.finishes, 2)
        self.assertEqual(self.backend.loads, 1)

    async def test_each_stream_keeps_its_output_through_its_queue_turn(self):
        first, writer = await self.client("First. ", end=False, streaming=True, output="headphones")
        await eventually(lambda: bool(self.backend.spoken))
        second, _ = await self.client("Second", output="speakers")
        third, _ = await self.client("Third")
        await eventually(lambda: self.service.queue.qsize() == 2)
        writer.write(encode({"type": "text", "text": "Still first."}))
        writer.write(encode({"type": "end"}))
        await writer.drain()
        for reader in (first, second, third):
            self.assertEqual(await self.reply(reader), {"type": "done"})
        self.assertEqual(
            self.backend.destinations,
            [
                ("First. ", "headphones"),
                ("Still first.", "headphones"),
                ("Second", "speakers"),
                ("Third", None),
            ],
        )
        self.assertEqual(self.backend.loads, 1)

    async def test_missing_output_preserves_model_and_next_request(self):
        self.backend.gate.clear()
        first, _ = await self.client("First", output="headphones")
        await eventually(lambda: bool(self.backend.spoken))
        missing, _ = await self.client("Do not speak", output="gone")
        next_reader, _ = await self.client("Next", output="speakers")
        self.backend.gate.set()
        self.assertEqual(await self.reply(first), {"type": "done"})
        reply = await self.reply(missing)
        self.assertEqual(reply["type"], "error")
        self.assertIn("unavailable", reply["message"])
        self.assertEqual(await self.reply(next_reader), {"type": "done"})
        self.assertEqual(self.backend.spoken, ["First", "Next"])
        self.assertEqual(self.backend.loads, 1)
        self.assertEqual(self.backend.unloads, 0)

    async def test_handshake_and_invalid_output_do_not_load_model(self):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.writers.append(writer)
        writer.write(encode({"type": "hello", "protocol": 2}))
        await writer.drain()
        self.assertEqual(await self.reply(reader), {"type": "ready", "protocol": 2})
        writer.write(encode({"type": "start", "stream": False, "output": 123}))
        await writer.drain()
        self.assertEqual((await self.reply(reader))["type"], "error")
        self.assertEqual(self.backend.loads, 0)

    async def test_stream_speaks_before_eof_and_keeps_its_place(self):
        first, writer = await self.client("One sentence. ", end=False, streaming=True)
        await eventually(lambda: self.backend.spoken == ["One sentence. "])
        second, _ = await self.client("Later request")
        await eventually(lambda: self.service.queue.qsize() == 1)
        writer.write(encode({"type": "text", "text": "Another sentence."}))
        writer.write(encode({"type": "end"}))
        await writer.drain()
        self.assertEqual(await self.reply(first), {"type": "done"})
        self.assertEqual(await self.reply(second), {"type": "done"})
        self.assertEqual(
            self.backend.spoken, ["One sentence. ", "Another sentence.", "Later request"]
        )

    async def test_unpunctuated_stream_flushes_after_pause(self):
        reader, writer = await self.client("Words without punctuation", end=False, streaming=True)
        await eventually(lambda: bool(self.backend.spoken))
        self.assertEqual(self.backend.spoken, ["Words without punctuation"])
        writer.write(encode({"type": "end"}))
        await writer.drain()
        self.assertEqual(await self.reply(reader), {"type": "done"})

    async def test_active_cancellation_stops_backend_and_preserves_queue(self):
        self.backend.gate.clear()
        _, writer = await self.client("active")
        await eventually(lambda: self.backend.spoken == ["active"])
        next_reader, _ = await self.client("next")
        writer.close()
        await writer.wait_closed()
        await eventually(lambda: self.backend.spoken == ["active", "next"])
        self.assertEqual(self.backend.unloads, 1)
        self.backend.gate.set()
        self.assertEqual(await self.reply(next_reader), {"type": "done"})

    async def test_large_queued_request_can_be_cancelled(self):
        self.backend.gate.clear()
        active, _ = await self.client("active")
        await eventually(lambda: bool(self.backend.spoken))
        _, writer = await self.client("queued", end=False)
        await eventually(lambda: self.service.queue.qsize() == 1)
        for _ in range(40):
            writer.write(encode({"type": "text", "text": "x" * 4096}))
        writer.write(encode({"type": "end"}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await eventually(lambda: len(self.service.connections) == 1)
        next_reader, _ = await self.client("next")
        self.backend.gate.set()
        self.assertEqual(await self.reply(active), {"type": "done"})
        self.assertEqual(await self.reply(next_reader), {"type": "done"})
        self.assertEqual(self.backend.spoken, ["active", "next"])
        self.assertEqual(self.backend.loads, 1)

    async def test_unloads_after_idle_then_loads_again(self):
        first, _ = await self.client("first")
        self.assertEqual(await self.reply(first), {"type": "done"})
        await eventually(lambda: self.backend.unloads == 1)
        second, _ = await self.client("second")
        self.assertEqual(await self.reply(second), {"type": "done"})
        self.assertEqual(self.backend.loads, 2)

    async def test_failure_is_reported_and_queue_recovers(self):
        first, _ = await self.client("fail")
        second, _ = await self.client("next")
        self.assertEqual((await self.reply(first))["type"], "error")
        self.assertEqual(await self.reply(second), {"type": "done"})
        self.assertEqual(self.backend.spoken, ["fail", "next"])

    async def test_empty_input_and_silent_pipe_do_not_load_or_block(self):
        await self.client(end=False, streaming=True)
        empty, _ = await self.client(" \n\t")
        self.assertEqual(await self.reply(empty), {"type": "done"})
        self.assertEqual(self.backend.loads, 0)
        next_reader, _ = await self.client("next")
        self.assertEqual(await self.reply(next_reader), {"type": "done"})


if __name__ == "__main__":
    unittest.main()
