"""Day-1 probe: a tiny HTTP service to measure Sprite wake behaviour.

Each response reports the process id and how long the process has run.
- Same pid + a jump in wall-clock time  -> warm wake (process was frozen)
- New pid + small uptime                -> cold wake (services restarted)
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8080"))
STARTED_WALL = time.time()
STARTED_MONO = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        body = json.dumps(
            {
                "path": self.path,
                "pid": os.getpid(),
                "started_at": STARTED_WALL,
                "wall_uptime_s": round(time.time() - STARTED_WALL, 3),
                "mono_uptime_s": round(time.monotonic() - STARTED_MONO, 3),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._reply()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(min(length, 1_000_000))
        self._reply()

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.command} {self.path}", flush=True)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
