"""Incremental phrase boundaries; Pocket applies the actual 180-token limit."""

import asyncio
import re

# A whitespace boundary avoids splitting decimals or words arriving in pieces.
BOUNDARY = re.compile(r"[.!?][\"'’”\])]*(?:\s+)|\n+")
MAX_BUFFER = 1200
PAUSE_SECONDS = 0.75


async def phrases(parts, streaming: bool):
    buffer = ""
    while True:
        try:
            if streaming and buffer.strip():
                part = await asyncio.wait_for(parts.get(), timeout=PAUSE_SECONDS)
            else:
                part = await parts.get()
        except TimeoutError:
            # An upstream pause is useful even for unpunctuated dictation.
            yield buffer
            buffer = ""
            continue
        if part is None:
            if buffer.strip():
                yield buffer
            return
        buffer += part
        if streaming:
            while match := BOUNDARY.search(buffer):
                text, buffer = buffer[: match.end()], buffer[match.end() :]
                if text.strip():
                    yield text
        # Bound memory even for a never-ending, unpunctuated input stream.
        while len(buffer) > MAX_BUFFER:
            cut = buffer.rfind(" ", 0, MAX_BUFFER + 1)
            if cut <= 0:
                cut = MAX_BUFFER
            text, buffer = buffer[:cut], buffer[cut:]
            if text.strip():
                yield text
