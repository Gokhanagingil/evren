-- 0015 — Sınıfa özgü kimlik anahtarları (U4.2, ADR-0019).
--
-- Kimlik aday dizini, bilinen anahtar türlerine ek olarak sınıfa özgü kimlik özniteliklerini
-- 'attr:<alan>' türüyle tutar (ör. attr:account_id bulut hesabı, attr:cluster_id küme, attr:subnet IP ağı).
-- Alan adı metadata sözlüğünden gelir; biçim ServiceNow alan adı kuralıyla sınırlıdır.
BEGIN;

ALTER TABLE ingest.identity_key DROP CONSTRAINT identity_key_key_type_check;
ALTER TABLE ingest.identity_key ADD CONSTRAINT identity_key_key_type_check
  CHECK (key_type IN ('name', 'compact', 'fqdn', 'serial', 'uuid', 'mac', 'ip', 'ext', 'correlation', 'vmobj', 'prefix')
         OR key_type ~ '^attr:[a-z][a-z0-9_]{0,79}$');

COMMIT;
