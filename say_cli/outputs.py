"""PipeWire output discovery and a small terminal picker; no model imports."""

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass

from .config import save_output, saved_output


@dataclass(frozen=True)
class Output:
    name: str
    label: str
    serial: int


class OutputUnavailable(RuntimeError):
    pass


def parse_outputs(graph: list) -> list[Output]:
    outputs = []
    for item in graph:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        props = (item.get("info") or {}).get("props", {})
        if props.get("media.class") != "Audio/Sink":
            continue
        name = props.get("node.name")
        serial = props.get("object.serial")
        if not isinstance(name, str) or not name or not isinstance(serial, int):
            continue
        label = props.get("node.description") or props.get("node.nick") or name
        outputs.append(Output(name, str(label), serial))
    return sorted(outputs, key=lambda output: (output.label.casefold(), output.name))


async def available_outputs() -> list[Output]:
    process = await asyncio.create_subprocess_exec(
        os.environ.get("SAY_PW_DUMP", "pw-dump"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    except TimeoutError as error:
        raise RuntimeError(
            "Timed out listing PipeWire outputs; check that PipeWire is running"
        ) from error
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Cannot list PipeWire outputs: {detail or 'pw-dump failed'}")
    try:
        graph = json.loads(stdout)
        if not isinstance(graph, list):
            raise TypeError("Expected a list of PipeWire objects")
        return parse_outputs(graph)
    except (ValueError, AttributeError, TypeError) as error:
        raise RuntimeError(f"Cannot read PipeWire outputs: {error}") from error


async def find_output(name: str) -> Output:
    for output in await available_outputs():
        if output.name == name:
            return output
    raise OutputUnavailable(
        f"Audio output {name!r} is unavailable; use --choose or --output default"
    )


def list_outputs():
    current = saved_output()
    outputs = asyncio.run(available_outputs())
    print(f"{'*' if current == 'default' else ' '} System default\n    default")
    for output in outputs:
        marker = "*" if output.name == current else " "
        print(f"{marker} {output.label}\n    {output.name}")
    if current != "default" and not any(output.name == current for output in outputs):
        print(f"* Saved output is unavailable\n    {current}")
    print("\n* Saved selection")


def choose_output():
    if not sys.stdin.isatty():
        raise RuntimeError("--choose needs a terminal; use --list-outputs and --output NAME")
    current = saved_output()
    outputs = asyncio.run(available_outputs())
    choices = [("default", "System default")] + [(output.name, output.label) for output in outputs]
    print("Choose the default output for say:", file=sys.stderr)
    for number, (name, label) in enumerate(choices, 1):
        marker = " (saved)" if name == current else ""
        print(f"  {number}. {label}{marker}", file=sys.stderr)
        if sum(label == other_label for _, other_label in choices) > 1:
            print(f"     {name}", file=sys.stderr)
    if not any(name == current for name, _ in choices):
        print(f"Saved output {current!r} is currently unavailable.", file=sys.stderr)
    while True:
        print("Select a number, or press Enter to cancel: ", end="", flush=True, file=sys.stderr)
        answer = sys.stdin.readline().strip()
        if not answer or answer.lower() == "q":
            print("Cancelled; preference unchanged.", file=sys.stderr)
            return
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(choices):
            name, label = choices[int(answer) - 1]
            if name != "default":
                # The device may have disappeared while the menu was open.
                asyncio.run(find_output(name))
            path = save_output(name)
            print(f"Saved output: {label}\nConfiguration: {path}", file=sys.stderr)
            return
        print(f"Enter a number from 1 to {len(choices)}.", file=sys.stderr)
