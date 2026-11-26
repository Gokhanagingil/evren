#!/usr/bin/env python3
"""Bağlayıcı çalıştırıcı (U5, ADR-0020): kaynaktan salt okunur çek → eşle → alım geçidine gönder.

    python tools/connector_run.py --connector zabbix --name zabbix-dc1            # gerçek kaynak → geçit
    python tools/connector_run.py --connector zabbix --simulate connectors/zabbix/samples --dry-run
    python tools/connector_run.py --connector zabbix --record build/zabbix-kayit --dry-run   # yanıtları kaydet
    python tools/connector_run.py --connector zabbix --replay build/zabbix-kayit --dry-run   # kayıttan oynat

Yapılandırma ortam değişkenleriyle; **sırlar yalnızca dosyadan** okunur, komut satırına ve çıktıya yazılmaz:

* Kaynak: ``EVREN_SOURCE_URL`` (https), ``EVREN_SOURCE_TOKEN_FILE`` (Bearer: Zabbix) veya ``EVREN_SOURCE_USER`` +
  ``EVREN_SOURCE_PASSWORD_FILE`` (Basic ya da oturum açma: vCenter), isteğe bağlı ``EVREN_SOURCE_CA_FILE``.
  Entra istemci kimliği (Defender): ``EVREN_SOURCE_TENANT_ID``, ``EVREN_SOURCE_CLIENT_ID``,
  ``EVREN_SOURCE_CLIENT_SECRET_FILE``; isteğe bağlı ``EVREN_SOURCE_LOGIN_URL``, ``EVREN_SOURCE_SCOPE``.
  Defender'da ``EVREN_SOURCE_URL`` verilmezse ``https://api.security.microsoft.com`` kullanılır.
* Geçit: ``EVREN_GATEWAY_URL``, ``EVREN_CONNECTOR_TOKEN_FILE`` (``evren connector issue`` ile verilen belirteç),
  isteğe bağlı ``EVREN_GATEWAY_CA_FILE``. Düz http yalnızca ``EVREN_GATEWAY_ALLOW_HTTP=1`` ile (geliştirme yığını).

Ham yük arşivi (ADR-0024): ``EVREN_ARCHIVE_DIR`` + ``EVREN_ARCHIVE_KEY_FILE`` tanımlıysa her ham nesne şifreli parça
dosyalarına yazılır ve kayıtlar ``raw_ref`` taşır. ``--from-archive <çalışma dizini>`` kaynağa bağlanmadan arşivi bugünkü
eşlemeyle yeniden işler (yeni çalışma; asıl çalışma tamamlanmamışsa delta).

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
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from _bootstrap import REPO_ROOT
from evren_core.connectors.archive import ArchiveError, ArchiveSource, ArchiveWriter, load_keys, open_run
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
LOGIN_URL = "https://login.microsoftonline.com"


class ConfigError(Exception):
    pass


def _env(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} tanımlı değil")
    return value


def _oauth(spec: SourceSpec, env: dict[str, str], transport: Transport, retries: int, *, simulated: bool
           ) -> dict[str, Any]:
    """Entra istemci kimliği akışı (Defender): kiracı, istemci kimliği, sır dosyası; belirteç ucu aynı taşımayla."""
    if simulated:
        return {"tenant": "00000000-0000-0000-0000-000000000000", "client_id": "simulasyon",
                "client_secret": "simulasyon",
                "login": HttpClient(LOGIN_URL, transport=transport, retries=retries)}
    return {"tenant": _env(env, "EVREN_SOURCE_TENANT_ID"), "client_id": _env(env, "EVREN_SOURCE_CLIENT_ID"),
            "client_secret": read_secret(env.get("EVREN_SOURCE_CLIENT_SECRET_FILE"), "İstemci sırrı"),
            "scope": env.get("EVREN_SOURCE_SCOPE") or None,
            "login": HttpClient(env.get("EVREN_SOURCE_LOGIN_URL") or LOGIN_URL, transport=transport, retries=retries)}


def source_http(spec: SourceSpec, args: argparse.Namespace, env: dict[str, str]
                ) -> tuple[HttpClient, dict[str, Any], RecordingTransport | None]:
    options: dict[str, Any] = {k: v for k, v in (("page_size", args.page_size), ("release", args.api_release))
                               if v and k in spec.options}
    if "lookback_days" in spec.options and env.get("EVREN_SOURCE_LOOKBACK_DAYS", "").strip():
        try:
            options["lookback_days"] = int(env["EVREN_SOURCE_LOOKBACK_DAYS"])
        except ValueError:
            raise ConfigError("EVREN_SOURCE_LOOKBACK_DAYS tam sayı olmalı") from None
    recorder = None
    if args.simulate or args.replay:
        if spec.auth == "login":
            options["credentials"] = ("simulasyon", "simulasyon")  # simülatör/kayıt gerçek kimlik istemez
        auth = {"bearer": "Bearer simulasyon", "basic": HttpClient.basic("simulasyon", "simulasyon")}.get(spec.auth)
        if args.simulate:
            if not spec.simulator:
                raise ConfigError(f"{spec.key} için simülatör yok")
            transport: Transport = spec.simulator(Path(args.simulate))
        else:
            replay = ReplayTransport.from_dir(args.replay)
            options = {**options, **replay.options}  # kayıttaki ayarlarla aynı istekler yapılmalı
            transport = replay
        if spec.auth == "oauth":
            options["oauth"] = _oauth(spec, env, transport, args.retries, simulated=True)
        return HttpClient(spec.default_url or SIMULATED_URL, transport=transport, auth_header=auth,
                          retries=args.retries), options, None
    url = env.get("EVREN_SOURCE_URL", "").strip() or spec.default_url or _env(env, "EVREN_SOURCE_URL")
    auth = None
    if spec.auth == "bearer":
        auth = HttpClient.bearer(read_secret(env.get("EVREN_SOURCE_TOKEN_FILE"), "Kaynak belirteci"))
    elif spec.auth == "basic":
        auth = HttpClient.basic(_env(env, "EVREN_SOURCE_USER"),
                                read_secret(env.get("EVREN_SOURCE_PASSWORD_FILE"), "Kaynak parolası"))
    elif spec.auth == "login":  # oturum açan kaynak (vCenter): kimlik yalnızca oturum açma isteğinde kullanılır
        options["credentials"] = (_env(env, "EVREN_SOURCE_USER"),
                                  read_secret(env.get("EVREN_SOURCE_PASSWORD_FILE"), "Kaynak parolası"))
    transport = UrllibTransport(ca_file=env.get("EVREN_SOURCE_CA_FILE") or None)
    if args.record:
        recorder = RecordingTransport(transport, args.record,
                                      {k: v for k, v in options.items() if k not in ("credentials", "oauth")})
        transport = recorder
    if spec.auth == "oauth":
        options["oauth"] = _oauth(spec, env, transport, args.retries, simulated=False)
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
    ap.add_argument("--run-id", type=uuid.UUID, help="çalışma kimliği (çalıştırıcı servisi verir; varsayılan: yeni)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--simulate", metavar="KLASÖR", help="kaynak yerine simülatör (örnek veri klasörü)")
    src.add_argument("--replay", metavar="KLASÖR", help="kaydedilmiş yanıtları oynat")
    src.add_argument("--record", metavar="KLASÖR", help="gerçek kaynak yanıtlarını kaydet")
    src.add_argument("--from-archive", metavar="ÇALIŞMA", help="ham yük arşivinden yeniden işle (kaynağa bağlanmaz; "
                                                                "EVREN_ARCHIVE_DIR altındaki <kaynak>/<tarih>/<run_id>)")
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
    run_id = args.run_id or uuid.uuid4()
    mode = args.mode
    writer: ArchiveWriter | None = None
    archive_hook = None
    recorder = None
    try:
        archive_root = env.get("EVREN_ARCHIVE_DIR", "").strip()
        if args.from_archive:
            if not archive_root:
                raise ConfigError("--from-archive için EVREN_ARCHIVE_DIR gerekli")
            source = ArchiveSource(open_run(archive_root, args.from_archive, load_keys(env.get("EVREN_ARCHIVE_KEY_FILE"))))
            if source.data_source != spec.data_source:
                raise ConfigError(f"Arşiv {source.data_source} kaynağına ait; --connector {spec.key} değil")
            client: Any = source
            mode = source.mode  # asıl çalışma tamamlanmamışsa delta
            archive_hook = source.ref  # yeniden işlenen kayıt asıl arşiv başvurusunu taşır
        else:
            http, options, recorder = source_http(spec, args, env)
            client = spec.build(http, options)
            if archive_root:
                writer = ArchiveWriter(Path(archive_root), spec.data_source, run_id,
                                       load_keys(env.get("EVREN_ARCHIVE_KEY_FILE")), mode=mode)
                archive_hook = writer.add
        mappings = load_mappings(REPO_ROOT / "connectors" / spec.mapping_dir, spec.data_source)
        port = gateway(args, env)
    except (ConfigError, ArchiveError, HttpError, MappingError, OSError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    report = None
    try:
        report = ConnectorRunner(client, mappings, port, archive=archive_hook).run(mode, args.since, run_id)
    except HttpError as exc:  # çalışma geçitte açılamadı
        print(f"Çalışma açılamadı: {exc}", file=sys.stderr)
        return 1
    finally:
        if recorder:
            recorder.save()
        if writer:
            writer.close(completed=bool(report and report.completed))
    out = report.as_json()
    if args.from_archive:
        out["from_archive"] = True
    print(json.dumps(out, ensure_ascii=False))
    return 0 if report.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
