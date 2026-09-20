"""Check installed wrappers and concurrent autostart without loading the model."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    executable = Path(sys.argv[1]) / "bin" / "say"
    with tempfile.TemporaryDirectory(prefix="say-package-") as directory:
        path = Path(directory)
        # Emulate a previous service that died without removing its socket.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
            stale.bind(str(path / "say.sock"))
        env = {**os.environ, "SAY_RUNTIME_DIR": directory}
        env.pop("PYTHONPATH", None)
        clients = []
        try:
            for _ in range(4):
                clients.append(
                    subprocess.Popen(
                        [str(executable)],
                        cwd=directory,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            for client in clients:
                stdout, stderr = client.communicate(timeout=10)
                if client.returncode or stdout or stderr:
                    raise RuntimeError(f"Installed command failed: {client.returncode}, {stderr!r}")
            if not (path / "say.sock").exists():
                raise RuntimeError("The service did not retain its socket")
        finally:
            for client in clients:
                if client.poll() is None:
                    client.kill()
                    client.wait()
            if (path / "service.lock").exists():
                pid = int((path / "service.lock").read_text().strip())
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while (path / "say.sock").exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Service did not shut down cleanly")
                    time.sleep(0.02)
        print("Installed command: stale socket recovery and concurrent autostart passed")


if __name__ == "__main__":
    main()
