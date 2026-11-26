"""Çalıştırıcı testleri: yol ayarı, metadata, sahte alım geçidi (yalnızca 127.0.0.1, düz http)."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/connector-runner"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from evren_core.metadata import load_metadata  # noqa: E402


@pytest.fixture(scope="session")
def metadata():
    return load_metadata(REPO / "metadata")


class FakeGateway:
    """Alım geçidinin bağlayıcıya görünen yüzü: çalışma aç, kayıt gönder, kapanış iste."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.auth: set[str] = set()
        gw = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                gw.auth.add(self.headers.get("authorization", ""))
                parts = self.path.strip("/").split("/")  # ingest v1 runs [id] [records|complete]
                if len(parts) == 3:
                    rid = body["data"]["run_id"]
                    gw.runs[rid] = {"records": 0, "complete": None, "source": body["source"]}
                    self._send(201, {"run_id": rid, "state": "open"})
                elif parts[-1] == "records":
                    gw.runs[parts[3]]["records"] += len(body["records"])
                    self._send(202, {"accepted": len(body["records"])})
                else:
                    gw.runs[parts[3]]["complete"] = body["data"]
                    self._send(202, {"run_id": parts[3], "state": "closing"})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gateway():
    gw = FakeGateway()
    yield gw
    gw.close()
