"""Stands in for macos/claude-voice-audio in tests: same stdin/stdout/stderr protocol."""

import json
import sys
import threading
import time

err = sys.stderr
err.write("EVT ready\n")
err.flush()


def mic():
    while True:  # 30 ms of silence at a time
        sys.stdout.buffer.write(b"\0" * 960)
        sys.stdout.buffer.flush()
        time.sleep(0.03)


threading.Thread(target=mic, daemon=True).start()
for line in sys.stdin:
    cmd = json.loads(line)
    if cmd["cmd"] == "speak":
        time.sleep(0.1)
        err.write(f"EVT done {cmd['id']}\n")
        err.flush()
