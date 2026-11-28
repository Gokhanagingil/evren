-- 0037 — Yapay zekâ önerileri: Evren MCP sunucusu ve öneri API'si (U9.2, F2-07, ADR-0043).
--
-- Yapay zekâ önerir, insan karar verir (kural 10, ISO/IEC 42001). MCP aracı veya kurum MCP uygulamasının REST
-- adaptörü golden record'a yazmaz; öneri ai.proposal'a düşer. Yetkili kişi öneriyi kabul edince Evren'in her zamanki
-- yolu çalışır (CI düzenlemesi Manual kaynak kaydı → uzlaştırıcı; eşleştirme kararı kuyruk kararı) ve kararı veren
-- kişinin yetkisiyle denetlenir. Öneriyi yapan kabul edemez. Öneri silinmez, karar değişmez.
--
-- Her öneride model ve sürüm, istem şablonu sürümü, girdi özeti, gerekçe ve karar saklanır.
BEGIN;

CREATE SCHEMA IF NOT EXISTS ai;

CREATE SEQUENCE IF NOT EXISTS ai.proposal_number_seq;
CREATE TABLE IF NOT EXISTS ai.proposal (
  sys_id          meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  number          text NOT NULL UNIQUE DEFAULT ('ONR' || lpad(nextval('ai.proposal_number_seq')::text, 7, '0')),
  kind            text NOT NULL CHECK (kind IN ('ci_update', 'match_decision')),
  target          text NOT NULL CHECK (target ~ '^[0-9a-f]{32}$' OR target ~ '^[0-9]{1,18}$'),
  target_label    text CHECK (target_label IS NULL OR length(target_label) <= 255),
  payload         jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  rationale       text NOT NULL CHECK (length(rationale) BETWEEN 10 AND 4000),
  model           text CHECK (model IS NULL OR length(model) <= 120),
  prompt_version  text CHECK (prompt_version IS NULL OR length(prompt_version) <= 60),
  input_summary   jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(input_summary) = 'object'),
  channel         text NOT NULL CHECK (channel IN ('mcp', 'api')),
  proposed_by     text NOT NULL,
  proposed_at     timestamptz NOT NULL DEFAULT now(),
  idempotency_key text CHECK (idempotency_key IS NULL OR idempotency_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'failed')),
  decided_by      text,
  decided_at      timestamptz,
  decision_note   text CHECK (decision_note IS NULL OR length(decision_note) BETWEEN 10 AND 2000),
  result          jsonb,
  CHECK ((status = 'pending') = (decided_by IS NULL)),
  CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
  CHECK (decided_by IS NULL OR decided_by <> proposed_by),
  UNIQUE (proposed_by, idempotency_key)
);
CREATE INDEX IF NOT EXISTS proposal_pending ON ai.proposal (kind, proposed_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS proposal_target ON ai.proposal (target);

-- Öneri silinmez; içeriği değişmez; karar bir kez verilir
CREATE OR REPLACE FUNCTION ai.tg_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'öneri silinemez' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status <> 'pending' THEN
    RAISE EXCEPTION 'karar verilmiş öneri değişmez' USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.kind, NEW.target, NEW.payload, NEW.rationale, NEW.model, NEW.prompt_version, NEW.input_summary, NEW.channel,
      NEW.proposed_by, NEW.proposed_at, NEW.number) IS DISTINCT FROM
     (OLD.kind, OLD.target, OLD.payload, OLD.rationale, OLD.model, OLD.prompt_version, OLD.input_summary, OLD.channel,
      OLD.proposed_by, OLD.proposed_at, OLD.number) THEN
    RAISE EXCEPTION 'önerinin içeriği değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS proposal_guard ON ai.proposal;
CREATE TRIGGER proposal_guard BEFORE UPDATE OR DELETE ON ai.proposal
  FOR EACH ROW EXECUTE FUNCTION ai.tg_proposal_guard();

-- API: öneriyi ekler ve yalnızca karar sütunlarını yazar; denetçi okur. MCP sunucusu API sürecindedir (ADR-0043):
-- ayrı bir veritabanı rolü yoktur, çağıranın rolleriyle API'nin yetki denetiminden geçer.
GRANT USAGE ON SCHEMA ai TO evren_app_api, evren_auditor_ro;
GRANT SELECT ON ai.proposal TO evren_app_api, evren_auditor_ro;
GRANT INSERT (kind, target, target_label, payload, rationale, model, prompt_version, input_summary, channel,
              proposed_by, idempotency_key) ON ai.proposal TO evren_app_api;
GRANT UPDATE (status, decided_by, decided_at, decision_note, result) ON ai.proposal TO evren_app_api;
GRANT USAGE ON SEQUENCE ai.proposal_number_seq TO evren_app_api;

COMMIT;
