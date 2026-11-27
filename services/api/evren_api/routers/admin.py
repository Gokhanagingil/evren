"""Yönetim paneli ve teknik belgeler (U8.2, ADR-0033).

* ``GET /api/v1/admin/attention``: kişiye düşen bekleyen işler (yalnızca yetkisi olduğu işler): eşleştirme kuyruğu,
  onay bekleyen veya taslakta kalan değişiklik setleri, sahiplik onayı süresi dolan CI'lar, eskime adayları, açık
  yetkisiz değişiklikler, devre kesicisi açık kaynaklar, süresi yaklaşan bağlayıcı kimlikleri, sistem durumu.
  Panel bunları "Size düşen işler" olarak gösterir; her biri ilgili ekrana götürür.
* ``GET /api/v1/docs`` ve ``GET /api/v1/docs/{ad}``: teknik belgeler (``docs/*.md``, ``docs/adr/*.md``) uygulama içinde.
  Oturum ister (mimari ve güvenlik tasarımı oturumsuz erişime açılmaz); ad yalnızca izinli biçimde ve docs altında
  çözülür (dizin dışına çıkılamaz).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends

from ..config import REPO_ROOT
from ..deps import Ctx, context
from ..errors import ApiError
from .system import system_status

router = APIRouter(prefix="/api/v1", tags=["admin"])

DOCS = REPO_ROOT / "docs"
DOC_NAME = re.compile(r"^(?:adr/)?[0-9A-Za-z][0-9A-Za-z.-]{0,120}$")


def _item(iid: str, label: str, count: int, href: str, hint: str, tone: str = "warn") -> dict[str, Any]:
    return {"id": iid, "label": label, "count": count, "href": href, "hint": hint, "tone": tone}


@router.get("/admin/attention")
def attention(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kişinin karar vermesi veya ilgilenmesi gereken bekleyen işler; yetkisi olmayan iş listelenmez."""
    items: list[dict[str, Any]] = []
    me = ctx.principal.audit_id
    with ctx.conn.cursor() as cur:
        if ctx.grant("match_queue", "review_match").allowed:
            cur.execute("SELECT count(*) FROM ingest.match_candidate WHERE status = 'open'")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("queue", "Eşleştirme kuyruğunda karar bekleyen kayıt", n, "#/kuyruk",
                                   "Evren bu kayıtları emin olamadığı için size soruyor."))
        if ctx.grant("schema", "schema_approve").allowed:
            cur.execute("SELECT count(*) FROM meta.schema_change_set WHERE state = 'in_review' AND created_by <> %s",
                        (me,))
            n = cur.fetchone()[0]
            if n:
                items.append(_item("schema_review", "Onayınızı bekleyen değişiklik seti", n, "#/sema",
                                   "Veri modeli değişikliği; hazırlayan kişi kendi setini onaylayamaz."))
            cur.execute("SELECT count(*) FROM meta.schema_change_set WHERE state = 'approved'")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("schema_publish", "Yayımlanmayı bekleyen onaylı değişiklik seti", n, "#/sema",
                                   "Yayın, sürüm kontrolüne eklenecek değişiklik dosyasını üretir.", "info"))
        if ctx.grant("schema", "schema_draft").allowed:
            cur.execute("SELECT count(*) FROM meta.schema_change_set WHERE state = 'draft' AND created_by = %s", (me,))
            n = cur.fetchone()[0]
            if n:
                items.append(_item("schema_draft", "Taslakta bıraktığınız değişiklik seti", n, "#/sema",
                                   "Onaya göndermediğiniz sürece hiçbir etkisi yoktur.", "info"))
        if ctx.grant("cmdb_ci", "retire").allowed:
            cur.execute("""SELECT count(*) FROM insight.staleness_case
                            WHERE status = 'open' AND stage = 'candidate'""")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("staleness", "Emekliye ayırma adayı", n, "#/eskime",
                                   "Gözlemci kaynakların hiçbiri uzun süredir görmüyor; emekliye ayırın veya koruyun."))
        if ctx.grant("anomaly", "anomaly_review").allowed:
            cur.execute("""SELECT count(*) FROM insight.anomaly
                            WHERE kind = 'unauthorized_change' AND status IN ('open', 'acknowledged')""")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("unauthorized", "İncelenmemiş yetkisiz değişiklik", n,
                                   "#/ci/kapsam=yetkisiz-degisiklik",
                                   "Onaylı bir değişiklik kaydı olmadan yapılan değişiklikler.", "bad"))
        if ctx.grant("data_source", "connector_admin").allowed:
            # Belirteç özeti içermeyen görünüm (göç 0018); API ham kimlik tablosunu okuyamaz
            cur.execute("""SELECT count(*) FROM ingest.connector_credential_status
                            WHERE state = 'active' AND expires_at < now() + interval '14 days'""")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("credentials", "Süresi 14 gün içinde dolacak bağlayıcı kimliği", n, "#/kaynaklar",
                                   "Süresi dolan kimlikle bağlayıcı veri gönderemez; yenisini verin."))
            cur.execute("""SELECT count(*) FROM ingest.data_source d
                            WHERE d.lifecycle IN ('active', 'shadow', 'retiring')
                              AND (SELECT breaker_state FROM ingest.source_run r WHERE r.data_source = d.name
                                    AND r.finished_at IS NOT NULL ORDER BY r.finished_at DESC LIMIT 1) = 'open'""")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("breaker", "Devre kesicisi açık veri kaynağı", n, "#/kaynaklar",
                                   "Son çalışma eksik veya şüpheli; o çalışmada yıkıcı işlem yapılmadı.", "bad"))
        if ctx.grant("iam", "iam_admin").allowed:
            # Süresi yaklaşan rol atamaları (yetkili roller süreli verilir; ADR-0034)
            cur.execute("""SELECT count(*) FROM iam.principal_role
                            WHERE expires_at > now() AND expires_at < now() + interval '14 days'""")
            n = cur.fetchone()[0]
            if n:
                items.append(_item("grants", "Süresi 14 gün içinde dolacak rol ataması", n, "#/kullanicilar/suresi-yaklasan",
                                   "Süre dolunca rol kendiliğinden düşer; gerekiyorsa gerekçeyle yenileyin.", "info"))
    if user_ := ctx.sys_user():
        # Yönetişim görevleri (U8.4): kişiye veya grubuna atanan açık görevler; süresi geçenler önce
        row = ctx.conn.execute(
            """SELECT count(*), count(*) FILTER (WHERE due_at < now()) FROM gov.task
                WHERE state = 'open' AND (assignee_user = %s OR assignee_group IN
                      (SELECT "group" FROM ref.sys_user_grmember WHERE "user" = %s))""",
            (user_["sys_id"], user_["sys_id"])).fetchone()
        if row and row[0]:
            late = f" ({row[1]} tanesinin süresi geçti)" if row[1] else ""
            items.append(_item("tasks", "Size atanmış açık görev", row[0], "#/gorevler",
                               f"Kararınızı Evren'de verin; ServiceNow'daki kaydı Evren kapatır.{late}",
                               "bad" if row[1] else "warn"))
    if ctx.grant("governance", "task_admin").allowed:
        row = ctx.conn.execute(
            """SELECT count(*) FILTER (WHERE delivery_status = 'failed' OR external_closed),
                      count(*) FILTER (WHERE assignee_basis = 'unassigned')
                 FROM gov.task WHERE state = 'open'""").fetchone()
        if row[0]:
            items.append(_item("task_attention", "Teslim edilemeyen veya ServiceNow'da kararsız kapatılan görev", row[0],
                               "#/gorevler/dikkat", "Kanalı denetleyin veya sorumluyla görüşün.", "warn"))
        failed = ctx.conn.execute("""SELECT count(DISTINCT campaign) FROM gov.campaign_run
                                      WHERE status = 'failed' AND started_at > now() - interval '7 days'""").fetchone()[0]
        if failed:
            items.append(_item("campaign_failed", "Son yedi günde çalışması başarısız olan kampanya", failed,
                               "#/gorevler/kampanyalar", "Kapsam sınırı aşıldı veya koşul geçersiz; kampanyayı açıp nedenini okuyun.",
                               "warn"))
        if row[1]:
            items.append(_item("task_unassigned", "Sorumlusu olmayan görev", row[1], "#/gorevler/atanmamis",
                               "CI'ın sahibi, yöneteni ve destek grubu yok; görevi bir kişiye veya gruba atayın.", "info"))
    if ctx.grant("cmdb_ci", "attest").allowed and (user := ctx.sys_user()):
        from .attestation import summary as attestation_summary  # döngüsel içe aktarmayı önlemek için burada
        mine = attestation_summary(ctx).get("mine") or {}
        n = int(mine.get("expired", 0)) + int(mine.get("not_reviewed", 0)) + int(mine.get("due_soon", 0))
        if n:
            items.append(_item("attestation", "Onayınızı bekleyen CI", n, "#/sahiplik-onayi",
                               f"{user['name']} olarak sorumlu olduğunuz CI'lar; süresi dolan ve hiç onaylanmamış olanlar."))
    if ctx.grant("platform", "read").allowed:
        status = system_status(ctx)
        if status["overall"] in ("warn", "bad"):
            bad = [c["label"] for c in status["components"] if c["state"] == status["overall"]]
            items.append(_item("system", f"Sistem durumu: {status['overall_label'].lower()}", len(bad),
                               "#/sistem-durumu", ", ".join(bad[:4]), "bad" if status["overall"] == "bad" else "warn"))
    order = {"bad": 0, "warn": 1, "info": 2}
    items.sort(key=lambda i: (order[i["tone"]], -i["count"]))
    return {"items": items}


def _title(path: Any) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


@router.get("/docs")
def list_docs(_ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Teknik belgelerin listesi (ad ve başlık)."""
    docs = [{"name": p.stem, "title": _title(p)} for p in sorted(DOCS.glob("[0-9]*.md"))]
    adrs = [{"name": f"adr/{p.stem}", "title": _title(p)} for p in sorted((DOCS / "adr").glob("[0-9]*.md"))]
    return {"docs": docs, "adrs": adrs}


@router.get("/docs/{name:path}")
def get_doc(name: str, _ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Bir teknik belgenin Markdown metni. Ad: ``06-guvenlik``, ``adr/0031-...``, ``adr/README``."""
    if not DOC_NAME.fullmatch(name) or ".." in name:
        raise ApiError(400, "gecersiz_belge", "Belge adı geçersiz.")
    path = (DOCS / f"{name}.md").resolve()
    if DOCS.resolve() not in path.parents or not path.is_file():
        raise ApiError(404, "bulunamadi", "Belge bulunamadı.")
    text = path.read_text(encoding="utf-8")
    return {"name": name, "title": _title(path), "markdown": text, "base": "adr" if name.startswith("adr/") else ""}
