"""No-cache static server used by the local procurement demo launcher."""

from __future__ import annotations

import argparse
import io
import os
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

CACHE_TOKEN_PLACEHOLDER = "__CACHE_BUST__"


class NoCacheStaticHandler(SimpleHTTPRequestHandler):
    """Serve the frontend without allowing stale HTML, JS, or CSS."""

    server_version = "ProcurementStatic/1.0"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):  # type: ignore[no-untyped-def]
        request_path = urlsplit(self.path).path
        if request_path in {"", "/", "/index.html"}:
            return self._send_versioned_index()
        return super().send_head()

    def _send_versioned_index(self) -> io.BytesIO:
        index_path = Path(self.directory) / "index.html"
        html = index_path.read_text(encoding="utf-8")
        cache_token = getattr(self.server, "cache_token", "")
        html = html.replace(CACHE_TOKEN_PLACEHOLDER, cache_token)
        body = html.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return io.BytesIO(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the procurement frontend without browser caching")
    parser.add_argument("port", type=int)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    directory = args.directory.resolve()
    cache_token = os.getenv("PROCUREMENT_FRONTEND_CACHE_BUST") or f"{time.time_ns():x}"
    handler = partial(NoCacheStaticHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    server.cache_token = cache_token
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
