#!/usr/bin/env python3
"""Evren konteyner giriş noktası (U1, ADR-0016) — tek imaj, birden çok servis:

    evren api          REST API + arayüz (uvicorn)
    evren worker       uzlaştırıcı çalışanı: insan istekleri, çalışma kapanışı, outbox → Kafka
    evren ingest       Kafka alım tüketicisi (evren.ingest.record.v1 → uzlaştırma)
    evren gateway      alım geçidi (bağlayıcılar → Kafka; TLS/mTLS isteğe bağlı)
    evren indexer      arama dizinleyicisi (CI olayları → OpenSearch); --reindex: tam yeniden dizinleme
    evren migrate      göçler + metadata tohumu + servis giriş rolleri (tek seferlik iş)
    evren connector    bağlayıcı kimlikleri: issue | list | revoke (yönetici bağlantısı)
    evren connector-run  bağlayıcı çalıştır: kaynak → eşleme → alım geçidi (--connector zabbix; --help)
    evren demo         sentetik demo verisini yükle (yalnızca EVREN_ALLOW_DEMO=1; boş geliştirme veritabanı)
    evren healthcheck  api|gateway|worker|ingest|indexer — konteyner sağlık denetimi

Sırlar dosyadan okunur (``*_FILE`` ortam değişkenleri); hiçbir sır komut satırına veya günlüğe yazılmaz.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
for p in ("packages/evren-core/src", "services/api", "services/reconciler", "services/ingest-gateway", "services/indexer",
          "tools"):
    sys.path.insert(0, str(APP / p))


def api() -> int:
    import uvicorn

    from evren_api import create_app
    from evren_api.config import ConfigError, Settings

    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, server_header=False,
                proxy_headers=False, log_level=os.environ.get("EVREN_LOG_LEVEL", "info").lower())
    return 0


def gateway() -> int:
    import ssl

    import uvicorn

    from evren_gateway import ConfigError, GatewaySettings, create_app

    try:
        settings = GatewaySettings.from_env(os.environ)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    tls: dict[str, object] = {}
    if settings.tls_cert_file:
        tls = {"ssl_certfile": settings.tls_cert_file, "ssl_keyfile": settings.tls_key_file}
        if settings.tls_client_ca_file:  # mTLS: istemci sertifikası zorunlu
            tls |= {"ssl_ca_certs": settings.tls_client_ca_file, "ssl_cert_reqs": ssl.CERT_REQUIRED}
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, server_header=False,
                proxy_headers=False, log_level=os.environ.get("EVREN_LOG_LEVEL", "info").lower(), **tls)
    return 0


def worker(args: list[str]) -> int:
    from evren_reconciler.worker import main

    return main(args)


def indexer(args: list[str]) -> int:
    from evren_indexer import main

    return main(args)


def connector(args: list[str]) -> int:
    import connector_credential

    return connector_credential.main(args)


def connector_run(args: list[str]) -> int:
    import connector_run as cr

    return cr.main(args)


def migrate(args: list[str]) -> int:
    import migrate as m

    return m.main(args)


def demo() -> int:
    if os.environ.get("EVREN_ALLOW_DEMO") != "1":
        print("Demo verisi yalnızca EVREN_ALLOW_DEMO=1 ile ve boş geliştirme veritabanına yüklenir.", file=sys.stderr)
        return 2
    import load_demo_pg

    return load_demo_pg.main([])


def healthcheck(target: str) -> int:
    if target == "api":
        try:
            port = int(os.environ.get("EVREN_PORT", "8088"))  # yalnızca sayı: URL sabit şema + loopback
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=3) as r:  # nosec B310
                return 0 if r.status == 200 else 1
        except (OSError, ValueError):
            return 1
    if target == "gateway":
        try:
            port = int(os.environ.get("EVREN_GATEWAY_PORT", "8089"))
            scheme = "https" if os.environ.get("EVREN_GATEWAY_TLS_CERT_FILE") else "http"
            if scheme == "https":  # sağlık denetimi yalnızca süreç içi; sertifika adı loopback'e uymaz
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                kwargs: dict[str, object] = {"context": ctx}
            else:
                kwargs = {}
            url = f"{scheme}://127.0.0.1:{port}/healthz"  # şema yalnızca http/https sabiti
            with urllib.request.urlopen(url, timeout=3, **kwargs) as r:  # nosec B310  # noqa: S310
                return 0 if r.status == 200 else 1
        except (OSError, ValueError):
            return 1
    if target in ("worker", "ingest", "indexer"):
        path = os.environ.get("EVREN_WORKER_HEALTH_FILE")
        poll = int(os.environ.get("EVREN_WORKER_POLL_SECONDS", "30"))
        if not path or not Path(path).exists():
            return 1
        return 0 if time.time() - Path(path).stat().st_mtime < 3 * poll + 10 else 1
    print("Kullanım: evren healthcheck api|gateway|worker|ingest|indexer", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "api":
        return api()
    if cmd == "worker":
        return worker(rest)
    if cmd == "ingest":
        return worker(["--ingest", *rest])
    if cmd == "gateway":
        return gateway()
    if cmd == "indexer":
        return indexer(rest)
    if cmd == "connector":
        return connector(rest)
    if cmd == "connector-run":
        return connector_run(rest)
    if cmd == "migrate":
        return migrate(rest)
    if cmd == "demo":
        return demo()
    if cmd == "healthcheck" and rest:
        return healthcheck(rest[0])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
