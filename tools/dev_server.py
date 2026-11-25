#!/usr/bin/env python3
"""Arayüz için yerel geliştirme sunucusu (yalnızca 127.0.0.1).

Üretim API'siyle aynı güvenlik başlıklarını uygular (evren_core.web). Yalnızca izin
verilen uzantıları sunar; dizin listelemez; kaynak YAML dosyalarını sunmaz.

Kullanım: python tools/dev_server.py [--port 8088]
"""
from __future__ import annotations

import argparse
import posixpath
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from _bootstrap import REPO_ROOT
from evren_core.web import SECURITY_HEADERS, STATIC_TYPES

WEB_ROOT = (REPO_ROOT / "apps" / "web").resolve()


class Handler(BaseHTTPRequestHandler):
    server_version = "evren-dev"
    sys_version = ""

    def _send(self, status: int, body: bytes, content_type: str, *, head: bool = False) -> None:
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _resolve(self) -> Path | None:
        path = posixpath.normpath(unquote(urlsplit(self.path).path))
        if path in ("/", "."):
            path = "/index.html"
        candidate = (WEB_ROOT / path.lstrip("/")).resolve()
        if WEB_ROOT not in candidate.parents and candidate != WEB_ROOT:
            return None
        if candidate.suffix not in STATIC_TYPES or not candidate.is_file():
            return None
        if "tests" in candidate.relative_to(WEB_ROOT).parts:
            return None
        return candidate

    def _serve(self, head: bool) -> None:
        target = self._resolve()
        if target is None:
            self._send(HTTPStatus.NOT_FOUND, "Bulunamadı".encode(), "text/plain; charset=utf-8", head=head)
            return
        self._send(HTTPStatus.OK, target.read_bytes(), STATIC_TYPES[target.suffix], head=head)

    def do_GET(self) -> None:
        self._serve(head=False)

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def log_message(self, fmt: str, *args: object) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)


def make_server(port: int, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"Evren arayüzü: http://127.0.0.1:{args.port}/  (Ctrl+C ile durdur)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
