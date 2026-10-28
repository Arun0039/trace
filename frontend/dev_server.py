"""Serve the static frontend and provide its configured backend API URL."""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


FRONTEND_ROOT = Path(__file__).resolve().parent


def api_base_url() -> str:
    value = os.environ.get("QA_API_BASE_URL", "http://127.0.0.1:8001").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("QA_API_BASE_URL must be a valid HTTP(S) URL.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("QA_API_BASE_URL must be an HTTP(S) origin without credentials or path.")
    return value


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_ROOT), **kwargs)

    def do_GET(self):
        request_path = urlsplit(self.path).path.rstrip("/") or "/"
        if request_path == "/config.js":
            body = f"window.QA_API_BASE_URL = {json.dumps(api_base_url())};\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path in {"/", "/about", "/privacy", "/terms", "/login", "/signup", "/workspace"}:
            self.path = "/index.html"
        super().do_GET()


def main() -> None:
    host = os.environ.get("QA_FRONTEND_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("QA_FRONTEND_PORT", "3000"))
    except ValueError as exc:
        raise ValueError("QA_FRONTEND_PORT must be an integer between 0 and 65535.") from exc
    if not 0 <= port <= 65535:
        raise ValueError("QA_FRONTEND_PORT must be an integer between 0 and 65535.")

    with ThreadingHTTPServer((host, port), FrontendHandler) as server:
        print(
            f"Frontend running at http://{host}:{server.server_port}/ "
            f"(API: {api_base_url() or 'same origin'})",
            flush=True,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
