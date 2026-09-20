"""Measure synthesis throughput and starvation without using an audio device."""

import asyncio
import base64
import json
import sys
import time
from importlib.metadata import version

from .backend import Pocket
from .common import encode

PASSAGE = (
    "I tried the new say command, but it sounded choppy, like audio that cannot keep up. "
    "This test measures when each piece of audio becomes available, so we can tell whether "
    "speech generation or playback is responsible for the gaps. "
    "We should be able to hear each sentence clearly, without unexpected interruptions."
)


async def measure(pocket, text):
    started = time.monotonic()
    pocket.worker.stdin.write(encode({"text": text, "reset_seed": True}))
    await pocket.worker.stdin.drain()
    first = None
    previous = None
    audio_seconds = 0.0
    arrival_gap = 0.0
    required_buffer = 0.0
    chunks = 0
    while True:
        event = await pocket.event()
        arrived = time.monotonic() - started
        if event["type"] == "done":
            break
        if event["type"] != "audio":
            raise RuntimeError("Unexpected worker event during benchmark")
        first = arrived if first is None else first
        if previous is not None:
            arrival_gap = max(arrival_gap, arrived - previous)
        # Starting playback immediately after the first chunk is safe only if
        # every later chunk arrives before the previous audio runs out.
        required_buffer = max(required_buffer, arrived - first - audio_seconds)
        previous = arrived
        audio_seconds += len(base64.b64decode(event["pcm"])) / (2 * pocket.sample_rate)
        chunks += 1
    if not chunks:
        raise RuntimeError("Pocket produced no audio")
    return {
        "text": text,
        "first_audio_seconds": round(first, 3),
        "generation_seconds": round(arrived, 3),
        "audio_seconds": round(audio_seconds, 3),
        "realtime_speed": round(audio_seconds / arrived, 3),
        "maximum_chunk_gap_seconds": round(arrival_gap, 3),
        "required_extra_buffer_seconds": round(required_buffer, 3),
    }


async def main():
    pocket = Pocket()
    try:
        await pocket.load()
        texts = [" ".join(sys.argv[1:])] if sys.argv[1:] else ["Hello World!", PASSAGE]
        for text in texts:
            result = await measure(pocket, text)
            print(json.dumps({"torch": version("torch"), **result}, indent=2), flush=True)
    finally:
        await pocket.close()


if __name__ == "__main__":
    asyncio.run(main())
