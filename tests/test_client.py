import contextlib
import io
import os
import socket
import sys
import threading
import unittest
from unittest.mock import patch

from say_cli.client import parse_args, send_input
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
                self.assertEqual(parse_args(argv), expected)

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
                sender = threading.Thread(target=send_input, args=(left, [], errors))
                sender.start()
                self.assertEqual(decode(replies.readline()), {"type": "start", "stream": True})
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
