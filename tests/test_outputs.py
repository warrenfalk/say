import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from say_cli.backend import Pocket
from say_cli.config import config_path, save_output, selected_output
from say_cli.outputs import (
    Output,
    OutputUnavailable,
    choose_output,
    find_output,
    list_outputs,
    parse_outputs,
)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.outputs = [
            Output("usb.headphones", "Headphones", 50),
            Output("hdmi.monitor", "Monitor", 70),
        ]
        self.discovery = patch(
            "say_cli.outputs.available_outputs", AsyncMock(return_value=self.outputs)
        )
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def test_discovery_includes_only_playback_nodes_with_identifiers(self):
        def node(name, media_class, serial, label=None):
            return {
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "suspended",
                    "props": {
                        "node.name": name,
                        "media.class": media_class,
                        "object.serial": serial,
                        "node.description": label,
                    },
                },
            }

        graph = [
            node("mic", "Audio/Source", 10),
            node("player", "Stream/Output/Audio", 11),
            node("hdmi", "Audio/Sink", 12, "Monitor"),
            node("virtual", "Audio/Sink", 13, "Mixer"),
            {"type": "PipeWire:Interface:Device"},
            {"type": "PipeWire:Interface:Node", "info": None},
        ]
        self.assertEqual(
            parse_outputs(graph), [Output("virtual", "Mixer", 13), Output("hdmi", "Monitor", 12)]
        )

    def test_picker_saves_named_device_and_can_restore_default(self):
        with (
            patch.object(sys, "stdin", Terminal("oops\n9\n2\n")),
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            choose_output()
        self.assertEqual(selected_output(None), "usb.headphones")
        self.assertIn("Headphones", messages.getvalue())
        self.assertIn("Saved output:", messages.getvalue())
        with patch.object(sys, "stdin", Terminal("1\n")), contextlib.redirect_stderr(io.StringIO()):
            choose_output()
        self.assertIsNone(selected_output(None))

    def test_cancel_does_not_create_or_change_configuration(self):
        for answer in ("", "\n", "q\n"):
            with (
                self.subTest(answer=answer),
                patch.object(sys, "stdin", Terminal(answer)),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                choose_output()
            self.assertFalse(config_path().exists())
        save_output("hdmi.monitor")
        before = config_path().read_bytes()
        with patch.object(sys, "stdin", Terminal("\n")), contextlib.redirect_stderr(io.StringIO()):
            choose_output()
        self.assertEqual(config_path().read_bytes(), before)

    def test_picker_requires_terminal_and_disconnection_does_not_save(self):
        with (
            patch.object(sys, "stdin", io.StringIO("2\n")),
            self.assertRaisesRegex(RuntimeError, "terminal"),
        ):
            choose_output()
        with (
            patch.object(sys, "stdin", Terminal("2\n")),
            patch("say_cli.outputs.find_output", side_effect=OutputUnavailable("disconnected")),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(OutputUnavailable),
        ):
            choose_output()
        self.assertFalse(config_path().exists())

    def test_list_marks_saved_device_even_when_disconnected(self):
        save_output("gone")
        with contextlib.redirect_stdout(io.StringIO()) as text:
            list_outputs()
        self.assertIn("Headphones\n    usb.headphones", text.getvalue())
        self.assertIn("* Saved output is unavailable\n    gone", text.getvalue())


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_output_does_not_load_the_model(self):
        pocket = Pocket()
        with (
            patch("say_cli.outputs.available_outputs", AsyncMock(return_value=[])),
            patch.object(pocket, "load", AsyncMock()) as load,
        ):
            with self.assertRaisesRegex(OutputUnavailable, "unavailable"):
                await pocket.speak("Do not speak", True, "gone")
            load.assert_not_called()

    async def test_output_name_resolves_to_current_node_instance(self):
        with patch(
            "say_cli.outputs.available_outputs",
            AsyncMock(
                side_effect=[
                    [Output("headphones", "Headphones", 50)],
                    [Output("headphones", "Headphones", 99)],
                ]
            ),
        ):
            self.assertEqual((await find_output("headphones")).serial, 50)
            self.assertEqual((await find_output("headphones")).serial, 99)
