#!/usr/bin/env python3
"""Bağlayıcı çalıştırıcı (U5, ADR-0020): kaynaktan salt okunur çek → eşle → alım geçidine gönder.

    python tools/connector_run.py --connector zabbix --name zabbix-dc1            # gerçek kaynak → geçit
    python tools/connector_run.py --connector zabbix --simulate connectors/zabbix/samples --dry-run
    python tools/connector_run.py --connector zabbix --record build/zabbix-kayit --dry-run   # yanıtları kaydet
    python tools/connector_run.py --connector zabbix --replay build/zabbix-kayit --dry-run   # kayıttan oynat

Yapılandırma ortam değişkenleriyle; **sırlar yalnızca dosyadan** okunur, komut satırına ve çıktıya yazılmaz:

* Kaynak: ``EVREN_SOURCE_URL`` (https), ``EVREN_SOURCE_TOKEN_FILE`` (Bearer: Zabbix) veya ``EVREN_SOURCE_USER`` +
  ``EVREN_SOURCE_PASSWORD_FILE`` (Basic ya da oturum açma: vCenter), isteğe bağlı ``EVREN_SOURCE_CA_FILE``.
* Geçit: ``EVREN_GATEWAY_URL``, ``EVREN_CONNECTOR_TOKEN_FILE`` (``evren connector issue`` ile verilen belirteç),
  isteğe bağlı ``EVREN_GATEWAY_CA_FILE``. Düz http yalnızca ``EVREN_GATEWAY_ALLOW_HTTP=1`` ile (geliştirme yığını).

``--simulate`` ve ``--replay`` kaynağa bağlanmaz; ``--dry-run`` geçide göndermez (``--out`` ile kayıtlar NDJSON
dosyasına yazılır; dosya kaynak verisi içerir, kurum dışına çıkarılmaz). ``--record`` gerçek kaynak yanıtlarını
kaydeder; kayıtlar kişisel veri içerebilir, repoya koymadan önce gözden geçirin.

Çıkış kodu: 0 çalışma tamamlandı · 1 çalışma tamamlanmadı (yarıda kalan çekme, reddedilen gönderim, eşleme hatası;
devre kesici yıkıcı işleri durdurur) · 2 yapılandırma hatası. Çıktı tek satır JSON rapordur.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from _bootstrap import REPO_ROOT
from evren_core.connectors.gateway import DryRunGateway, GatewayClient
from evren_core.connectors.http import (
    HttpClient,
    HttpError,
    RecordingTransport,
    ReplayTransport,
    Transport,
    UrllibTransport,
    read_secret,
)
from evren_core.connectors.mapping import MappingError
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources import SOURCES, SourceSpec

SIMULATED_URL = "https://kaynak-simulatoru.invalid"


class ConfigError(Exception):
    pass


def _env(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} tanımlı değil")
    return value


def source_http(spec: SourceSpec, args: argparse.Namespace, env: dict[str, str]
                ) -> tuple[HttpClient, dict[str, Any], RecordingTransport | None]:
    options: dict[str, Any] = {k: v for k, v in (("page_size", args.page_size), ("release", args.api_release))
                               if v and k in spec.options}
    recorder = None
    if args.simulate or args.replay:
        if spec.auth == "login":
            options["credentials"] = ("simulasyon", "simulasyon")  # simülatör/kayıt gerçek kimlik istemez
        auth = None if spec.auth == "login" else "Bearer simulasyon"
        if args.simulate:
            if not spec.simulator:
                raise ConfigError(f"{spec.key} için simülatör yok")
            return HttpClient(SIMULATED_URL, transport=spec.simulator(Path(args.simulate)), auth_header=auth,
                              retries=args.retries), options, None
        replay = ReplayTransport.from_dir(args.replay)
        options = {**options, **replay.options}  # kayıttaki ayarlarla aynı istekler yapılmalı
        return HttpClient(SIMULATED_URL, transport=replay, auth_header=auth, retries=args.retries), options, None
    url = _env(env, "EVREN_SOURCE_URL")
    auth = None
    if spec.auth == "bearer":
        auth = HttpClient.bearer(read_secret(env.get("EVREN_SOURCE_TOKEN_FILE"), "Kaynak belirteci"))
    elif spec.auth == "basic":
        auth = HttpClient.basic(_env(env, "EVREN_SOURCE_USER"),
                                read_secret(env.get("EVREN_SOURCE_PASSWORD_FILE"), "Kaynak parolası"))
    else:  # oturum açan kaynak (vCenter): kimlik istemciye verilir, yalnızca oturum açma isteğinde kullanılır
        options["credentials"] = (_env(env, "EVREN_SOURCE_USER"),
                                  read_secret(env.get("EVREN_SOURCE_PASSWORD_FILE"), "Kaynak parolası"))
    transport: Transport = UrllibTransport(ca_file=env.get("EVREN_SOURCE_CA_FILE") or None)
    if args.record:
        recorder = RecordingTransport(transport, args.record,
                                      {k: v for k, v in options.items() if k != "credentials"})
        transport = recorder
    return HttpClient(url, transport=transport, auth_header=auth, retries=args.retries), options, recorder


def gateway(args: argparse.Namespace, env: dict[str, str]) -> GatewayClient | DryRunGateway:
    if args.dry_run:
        return DryRunGateway(args.out, batch_size=args.batch_size)
    url = _env(env, "EVREN_GATEWAY_URL")
    token = read_secret(env.get("EVREN_CONNECTOR_TOKEN_FILE"), "Bağlayıcı belirteci")
    transport = UrllibTransport(ca_file=env.get("EVREN_GATEWAY_CA_FILE") or None,
                                allow_http=env.get("EVREN_GATEWAY_ALLOW_HTTP") == "1")
    http = HttpClient(url, transport=transport, auth_header=HttpClient.bearer(token), retries=args.retries)
    return GatewayClient(http, args.name or args.connector, batch_size=args.batch_size)


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connector", required=True, choices=sorted(SOURCES), help="kaynak istemcisi")
    ap.add_argument("--name", help="bağlayıcı adı (zarftaki kaynak; varsayılan: --connector)")
    ap.add_argument("--mode", choices=["snapshot", "delta"], default="snapshot")
    ap.add_argument("--since", type=datetime.fromisoformat, help="delta için başlangıç (ISO 8601)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--simulate", metavar="KLASÖR", help="kaynak yerine simülatör (örnek veri klasörü)")
    src.add_argument("--replay", metavar="KLASÖR", help="kaydedilmiş yanıtları oynat")
    src.add_argument("--record", metavar="KLASÖR", help="gerçek kaynak yanıtlarını kaydet")
    ap.add_argument("--dry-run", action="store_true", help="geçide gönderme; yalnızca rapor")
    ap.add_argument("--out", metavar="DOSYA", help="kuru çalıştırmada kayıtları NDJSON olarak yaz")
    ap.add_argument("--page-size", type=int, help="kaynak sayfa boyutu")
    ap.add_argument("--api-release", help="kaynak API sürümü (vCenter VI/JSON, ör. 8.0.1.0)")
    ap.add_argument("--batch-size", type=int, default=500, help="geçide toplu gönderim boyutu (geçit sınırı ≤)")
    ap.add_argument("--retries", type=int, default=4, help="geçici hatada yeniden deneme sayısı (geri çekilmeli)")
    args = ap.parse_args(argv)
    env = dict(os.environ if env is None else env)
    logging.basicConfig(level=env.get("EVREN_LOG_LEVEL", "WARNING").upper(), stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.out and not args.dry_run:
        ap.error("--out yalnızca --dry-run ile kullanılır")
    if args.batch_size < 1 or (args.page_size is not None and args.page_size < 1) or not 0 <= args.retries <= 10:
        ap.error("--batch-size ve --page-size pozitif, --retries 0–10 olmalı")
    spec = SOURCES[args.connector]
    try:
        http, options, recorder = source_http(spec, args, env)
        client = spec.build(http, options)
        mappings = load_mappings(REPO_ROOT / "connectors" / spec.mapping_dir, spec.data_source)
        port = gateway(args, env)
    except (ConfigError, HttpError, MappingError, OSError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    try:
        report = ConnectorRunner(client, mappings, port).run(args.mode, args.since)
    except HttpError as exc:  # çalışma geçitte açılamadı
        print(f"Çalışma açılamadı: {exc}", file=sys.stderr)
        return 1
    finally:
        if recorder:
            recorder.save()
    print(json.dumps(report.as_json(), ensure_ascii=False))
    return 0 if report.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
