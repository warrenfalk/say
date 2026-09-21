"""Exercise real routing on a private graph with silent virtual outputs.

Run under dbus-run-session. WirePlumber's policy-only profile never opens audio
hardware. Synthetic PCM replaces the TTS worker; discovery and playback are real.
"""

import asyncio
import base64
import codecs
import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from say_cli.backend import Pocket, terminate

PIPEWIRE_CONFIG = """
context.properties = {
    core.daemon = true
    core.name = say-routing-test
    default.clock.rate = 24000
}
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.* = support/libspa-support
}
context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-spa-node-factory }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-link-factory }
    { name = libpipewire-module-access args = { access.force = unrestricted } }
]
context.objects = [
    { factory = spa-node-factory args = {
        factory.name = support.node.driver
        node.name = Dummy-Driver
        node.group = pipewire.dummy
        priority.driver = 20000
    } }
    SINKS
]
"""


async def command(*args):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    finally:
        await terminate(process)
    if process.returncode:
        raise RuntimeError(f"{args[0]}: {stderr.decode()}")
    return stdout


async def graph():
    return json.loads(await command("pw-dump"))


async def until(predicate):
    async with asyncio.timeout(8):
        while not (value := await predicate()):
            await asyncio.sleep(0.025)
        return value


class Input:
    def write(self, data):
        pass

    async def drain(self):
        pass


class SyntheticPocket(Pocket):
    def __init__(self, chunks, before_load=None):
        super().__init__()
        self.remaining = chunks
        self.before_load = before_load

    async def load(self):
        if self.before_load is not None:
            await self.before_load()
        self.sample_rate = 24000
        self.worker = SimpleNamespace(stdin=Input())

    async def event(self):
        if self.remaining == 0:
            return {"type": "done"}
        self.remaining -= 1
        await asyncio.sleep(0.005)
        return {"type": "audio", "pcm": base64.b64encode(bytes(3840)).decode()}

    async def close(self):
        self.worker = None
        await super().close()


async def exercise(
    output, expected_target, *, remove_during_playback=False, remove_before_load=False
):
    async def remove():
        await command("pw-cli", "destroy", str(expected_target))

    pocket = SyntheticPocket(
        1000 if remove_during_playback else 10,
        before_load=remove if remove_before_load else None,
    )
    monitor = await asyncio.create_subprocess_exec(
        "pw-dump", "-m", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    events = []
    ready = asyncio.Event()

    async def watch():
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        while chunk := await monitor.stdout.read(65536):
            buffer += decoder.decode(chunk)
            while buffer.strip():
                buffer = buffer.lstrip()
                try:
                    batch, end = json.JSONDecoder().raw_decode(buffer)
                except json.JSONDecodeError:
                    break
                events.extend(batch)
                buffer = buffer[end:]
                ready.set()

    watcher = asyncio.create_task(watch())
    playback = None
    try:
        await asyncio.wait_for(ready.wait(), 5)

        async def speak():
            await pocket.speak("Synthetic speech", True, output)
            await pocket.finish()

        playback = asyncio.create_task(speak())

        def player_nodes():
            return {
                event["id"]
                for event in events
                if event.get("type") == "PipeWire:Interface:Node"
                and (event.get("info") or {}).get("props", {}).get("media.class")
                == "Stream/Output/Audio"
            }

        def destinations():
            nodes = player_nodes()
            return {
                info["input-node-id"]
                for event in events
                if event.get("type") == "PipeWire:Interface:Link"
                and (info := event.get("info"))
                and info.get("output-node-id") in nodes
            }

        async def linked():
            return expected_target in destinations()

        if remove_during_playback:
            await until(linked)
            await remove()
        if remove_during_playback or remove_before_load:
            try:
                await asyncio.wait_for(playback, 5)
            except RuntimeError as error:
                assert output in str(error), error
            else:
                raise AssertionError("Disconnected output did not produce an error")
        else:
            await asyncio.wait_for(playback, 5)
            await until(linked)
        await pocket.close()
        await terminate(monitor)
        await watcher
        actual = destinations()
        assert actual <= {expected_target}, f"Unexpected routing: {actual}"
        if not remove_before_load:
            assert actual == {expected_target}, f"No link to expected output: {actual}"
    finally:
        if playback is not None:
            playback.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await playback
        await pocket.close()
        await terminate(monitor)
        await watcher


async def run(directory):
    sinks = []
    for name, priority in (("default", 1000), ("selected", 100), ("vanishing", 50)):
        sinks.append(f"""{{ factory = adapter args = {{
            factory.name = support.null-audio-sink
            node.name = say-test-{name}
            node.description = "Test {name}"
            media.class = Audio/Sink
            node.virtual = true
            priority.session = {priority}
            audio.position = [ FL FR ]
            adapter.auto-port-config = {{ mode = dsp monitor = true position = preserve }}
        }} }}""")
    config = directory / "pipewire.conf"
    config.write_text(PIPEWIRE_CONFIG.replace("SINKS", "\n".join(sinks)))
    wp_config = directory / "config/wireplumber/wireplumber.conf.d/90-say-tests.conf"
    wp_config.parent.mkdir(parents=True)
    wp_config.write_text("""wireplumber.profiles = { policy = {
        support.portal-permissionstore = disabled
        script.client.access-portal = disabled
        support.reserve-device = disabled
        support.logind = disabled
    } }""")
    processes = []
    with (directory / "audio.log").open("wb") as log:
        try:
            processes.append(
                await asyncio.create_subprocess_exec(
                    "pipewire", "-c", str(config), stdout=log, stderr=log
                )
            )

            async def socket_ready():
                return (directory / "say-routing-test").exists()

            await until(socket_ready)
            processes.append(
                await asyncio.create_subprocess_exec(
                    "wireplumber", "-p", "policy", stdout=log, stderr=log
                )
            )

            async def targets_ready():
                targets = {
                    (event.get("info") or {}).get("props", {}).get("node.name"): event["id"]
                    for event in await graph()
                    if event.get("type") == "PipeWire:Interface:Node"
                }
                metadata = [
                    event
                    for event in await graph()
                    if event.get("type") == "PipeWire:Interface:Metadata"
                ]
                return targets if "say-test-vanishing" in targets and len(metadata) >= 3 else None

            targets = await until(targets_ready)
            await exercise(None, targets["say-test-default"])
            await exercise(
                "say-test-selected", targets["say-test-selected"], remove_during_playback=True
            )
            await exercise(
                "say-test-vanishing", targets["say-test-vanishing"], remove_before_load=True
            )
            print("PipeWire: default output, selected output, disconnect, and no fallback passed")
        except Exception:
            print((directory / "audio.log").read_text())
            raise
        finally:
            for process in reversed(processes):
                await terminate(process)


def main():
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("Run this test under dbus-run-session")
    with tempfile.TemporaryDirectory(prefix="say-routing-") as temporary:
        directory = Path(temporary)
        with patch.dict(
            os.environ,
            {
                "XDG_RUNTIME_DIR": temporary,
                "PIPEWIRE_RUNTIME_DIR": temporary,
                "PIPEWIRE_REMOTE": "say-routing-test",
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_STATE_HOME": str(directory / "state"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "DBUS_SYSTEM_BUS_ADDRESS": os.environ["DBUS_SESSION_BUS_ADDRESS"],
                "GIO_USE_VFS": "local",
            },
        ):
            asyncio.run(run(directory))


class RoutingTests(unittest.TestCase):
    def test_routing_and_disconnection(self):
        main()


if __name__ == "__main__":
    unittest.main()
