"""Bounded-memory input storage, so queued producers can still disconnect."""

import asyncio
import json
import tempfile


class TextInput:
    def __init__(self):
        # Request cleanup owns this file's lifetime, including cancelled jobs.
        self.file = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=64 * 1024, mode="w+t", encoding="utf-8"
        )
        self.read_position = 0
        self.write_position = 0
        self.ended = False
        self.changed = asyncio.Event()

    def put(self, text: str | None):
        if text is None:
            self.ended = True
        else:
            self.file.seek(self.write_position)
            self.file.write(json.dumps(text, ensure_ascii=False) + "\n")
            self.write_position = self.file.tell()
        self.changed.set()

    async def get(self):
        while self.read_position == self.write_position:
            if self.ended:
                return None
            self.changed.clear()
            await self.changed.wait()
        self.file.seek(self.read_position)
        text = json.loads(self.file.readline())
        self.read_position = self.file.tell()
        # Reuse storage once the consumer catches up with the producer.
        if self.read_position == self.write_position:
            self.file.seek(0)
            self.file.truncate()
            self.read_position = self.write_position = 0
        return text

    def close(self):
        self.file.close()
