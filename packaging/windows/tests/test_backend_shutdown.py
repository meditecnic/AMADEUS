"""Exercise frozen-entry lifetime handling without importing the real application."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ENTRY_DIR = Path(__file__).resolve().parents[1]

# Only the Windows kernel and application/server are stand-ins. The real
# watch_parent and serve run in a child with real output pipes and a watched PID.
PROBE = r'''
import asyncio
import ctypes
import os
import sys
import threading
import time
import types

sys.path.insert(0, sys.argv[1])
import backend_entry

pid = int(sys.argv[2])
def open_process(*args):
    assert args[-1] == pid
    return 1
def wait_for_process(handle, milliseconds):
    time.sleep(milliseconds / 1000)
    try:
        os.kill(pid, 0)
        return 0x102
    except ProcessLookupError:
        return 0
def close_handle(handle):
    return True
kernel = types.SimpleNamespace(OpenProcess=open_process,
    WaitForSingleObject=wait_for_process, CloseHandle=close_handle)
ctypes.WinDLL = lambda *args, **kwargs: kernel

class Server:
    started = False
    should_exit = False
    def __init__(self, config):
        pass
    async def serve(self, sockets):
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.01)

sys.modules['uvicorn'] = types.SimpleNamespace(Server=Server, Config=lambda *a, **kw: None)
sys.modules['app.main'] = types.SimpleNamespace(app=object())
shutdown = threading.Event()
threading.Thread(target=backend_entry.watch_parent, args=(pid, shutdown), daemon=True).start()
raise SystemExit(asyncio.run(backend_entry.serve(shutdown, 0)))
'''


@unittest.skipUnless(os.name == "posix", "Linux probe substitutes the Windows wait API")
class BackendShutdownTests(unittest.TestCase):
    def test_parent_exit_with_closed_output_pipes_still_stops_server(self):
        with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                              stdin=subprocess.PIPE) as parent, \
                subprocess.Popen([sys.executable, "-u", "-c", PROBE, str(ENTRY_DIR), str(parent.pid)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as backend:
            try:
                # Bound startup too, so a regression cannot hang the test runner.
                import select
                self.assertTrue(select.select([backend.stdout], [], [], 3)[0], "no READY")
                self.assertTrue(backend.stdout.readline().startswith(b"AMADEUS_BACKEND_READY "))
                backend.stdout.close()
                backend.stderr.close()
                parent.stdin.close()
                self.assertEqual(parent.wait(timeout=3), 0)
                self.assertEqual(backend.wait(timeout=3), 0)
            finally:
                if backend.poll() is None:
                    backend.kill()
                    backend.wait(timeout=3)
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
