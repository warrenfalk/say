import contextlib
import io
import os
import socket
import sys
import threading
import unittest
from unittest.mock import patch

from say_cli.client import main, negotiate, parse_args, send_input
from say_cli.common import decode


class ClientTests(unittest.TestCase):
    def test_arguments_and_option_terminator(self):
        cases = [
            (["Hello", "World"], ["Hello", "World"]),
            (["Hello World!"], ["Hello World!"]),
            (["--", "--literal", "text"], ["--literal", "text"]),
            (["Hello", "--help"], ["Hello", "--help"]),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(parse_args(argv).text, expected)

    def test_output_option_and_literal_flags(self):
        options = parse_args(["--output", "headphones", "--", "Hello", "--choose"])
        self.assertEqual(options.output, "headphones")
        self.assertEqual(options.text, ["Hello", "--choose"])
        self.assertFalse(options.choose)
        self.assertEqual(parse_args(["--output", "default", "Hello"]).output, "default")

    def test_management_commands_reject_speech_and_conflicting_flags(self):
        for args in (
            ["--choose", "Hello"],
            ["--list-outputs", "Hello"],
            ["--choose", "--output", "headphones"],
            ["--out", "headphones"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(args)
                self.assertEqual(error.exception.code, 2)

    def test_old_service_is_rejected_before_sending_speech(self):
        left, right = socket.socketpair()
        with left, right, left.makefile("rb") as replies:
            right.sendall(b'{"type":"error","message":"Invalid speech request"}\n')
            with self.assertRaisesRegex(RuntimeError, "restart say-service"):
                negotiate(left, replies)
            message = decode(right.recv(4096))
            self.assertEqual(message["type"], "hello")
            self.assertNotIn("text", message)

    def test_selection_commands_do_not_start_service(self):
        for flag, function in (("--choose", "choose_output"), ("--list-outputs", "list_outputs")):
            with (
                self.subTest(flag=flag),
                patch.object(sys, "argv", ["say", flag]),
                patch(f"say_cli.client.{function}") as action,
                patch("say_cli.client.connect") as connect,
            ):
                self.assertEqual(main(), 0)
                action.assert_called_once_with()
                connect.assert_not_called()

    def test_unknown_leading_options_are_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parse_args(["--unknown", "text"])
        self.assertEqual(error.exception.code, 2)

    def test_utf8_pipe_is_forwarded_before_it_closes(self):
        read_fd, write_fd = os.pipe()
        errors = []
        with contextlib.ExitStack() as stack:
            left, right = socket.socketpair()
            stack.enter_context(left)
            stack.enter_context(right)
            stdin = stack.enter_context(os.fdopen(read_fd, "rb", buffering=0))
            output = stack.enter_context(os.fdopen(write_fd, "wb", buffering=0))
            replies = stack.enter_context(right.makefile("rb"))
            right.settimeout(3)
            with patch.object(sys, "stdin", stdin):
                sender = threading.Thread(target=send_input, args=(left, [], errors, "headphones"))
                sender.start()
                self.assertEqual(
                    decode(replies.readline()),
                    {
                        "type": "start",
                        "stream": True,
                        "output": "headphones",
                    },
                )
                output.write(b"Caf")
                self.assertEqual(decode(replies.readline())["text"], "Caf")
                output.write(b"\xc3")
                output.write(b"\xa9.")
                self.assertEqual(decode(replies.readline())["text"], "é.")
                output.close()
                self.assertEqual(decode(replies.readline()), {"type": "end"})
                sender.join(timeout=3)
                self.assertFalse(sender.is_alive())
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
