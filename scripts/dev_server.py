"""
Local full-stack dev server for the UNC Research Graph.

Mirrors vercel.json routing in a single process so the *connected* backend +
frontend can be exercised locally (in production Vercel does this routing):

  - API paths (/health, /stats, /freshness, /match/*, /unit/*, /api/*) are
    answered by api/index.py's handle().
  - Everything else is served as a static file from frontend/ (graph.json,
    index.html, app.js, styles.css, …).

Usage:
  python scripts/dev_server.py [port]      # default 8077

Stdlib only — no dependencies, no API keys, no config. This is a dev/verify
helper; the deployed app routes via vercel.json, not this file.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))
from api.index import handle  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dev_server")

API_PREFIXES = ("/health", "/stats", "/freshness", "/match/", "/unit/", "/units",
                "/faculty", "/partnerships", "/api/")


def _is_api(path: str) -> bool:
    # Match an API route exactly or as a "/sub" path — but NOT as a loose prefix,
    # so a static file like /partnerships.json is served, not routed to the API.
    # (Mirrors Vercel, whose route regexes are fully anchored.)
    for p in API_PREFIXES:
        if p.endswith("/"):
            if path == p[:-1] or path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


class DevHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def _route(self, method: str) -> bool:
        parsed = urlparse(self.path)
        if not _is_api(parsed.path):
            return False
        qs = parse_qs(parsed.query)
        status, headers, body = handle(method, parsed.path, qs)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):
        if not self._route("GET"):
            super().do_GET()

    def do_OPTIONS(self):
        if not self._route("OPTIONS"):
            self.send_response(204)
            self.end_headers()

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8077
    httpd = ThreadingHTTPServer(("127.0.0.1", port), DevHandler)
    logger.info("dev server on http://127.0.0.1:%d  (frontend + API, same origin)", port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
