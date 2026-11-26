-- 0017 — Sahiplik onayı durumu (F0-07 doğrulaması, ADR-0019).
--
-- ServiceNow'da onayın asıl durumu cmdb_ci.attestation_status alanıdır (Attested / Rejected / Not Yet Reviewed);
-- attested boole alanı eski ve onaydan sonra güncellenmez. Değerleri yalnızca Evren'deki onay akışı yazar
-- (Manual kaynak kaydı → uzlaştırma; reconciliation_rules.yaml).
BEGIN;

ALTER TABLE cmdb.cmdb_ci ADD COLUMN IF NOT EXISTS attestation_status text;
CREATE INDEX IF NOT EXISTS cmdb_ci_attestation_status ON cmdb.cmdb_ci (attestation_status)
  WHERE attestation_status IS NOT NULL;

COMMIT;
