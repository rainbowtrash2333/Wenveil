"""Local-only HTTP wrapper for browser development.

This is intentionally separate from the production JSON Lines sidecar. It
accepts browser-selected files in memory, writes them to a temporary folder,
and delegates to ``sidecar.handle_request``. It binds to loopback only and
never logs request bodies or source values.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sidecar import handle_request, safe_error


class SidecarHandler(BaseHTTPRequestHandler):
    server_version = "WenveilHttpDev/0.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/sidecar":
            self._send_error(404, "本地桥接地址不存在")
            return
        response_started = False
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            response_started = True

            def emit(event: dict[str, Any]) -> None:
                self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()

            result = handle_request(payload, emit)
            emit({"type": "result", "result": result})
        except Exception as error:
            print(f"HTTP adapter request failed type={type(error).__name__} summary={safe_error(error)}", file=sys.stderr, flush=True)
            if response_started and not self.wfile.closed:
                try:
                    self.wfile.write((json.dumps({"type": "error", "message": safe_error(error)}, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    pass
            elif not response_started and not self.wfile.closed:
                self._send_error(400, safe_error(error))

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {"http://localhost:5173", "http://127.0.0.1:5173"}:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def _send_error(self, code: int, message: str) -> None:
        if self.wfile.closed:
            return
        body = json.dumps({"type": "error", "message": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    address = ("127.0.0.1", 8765)
    print(f"Wenveil HTTP dev adapter listening on http://{address[0]}:{address[1]}", flush=True)
    ThreadingHTTPServer(address, SidecarHandler).serve_forever()


if __name__ == "__main__":
    main()
