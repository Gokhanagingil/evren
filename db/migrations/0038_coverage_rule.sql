-- 0038 — Kapsam boşluğu kural türü (P1.1, F1-30, ADR-0046).
--
-- Kapalı döngü kapsam güvencesinin ilk parçası: "kapsam politikası" bir kalite kuralıdır (coverage_gap). Kapsamdaki
-- CI için zorunlu kaynak rolleri ve görülme tazeliği tanımlanır; eksik rollerin hepsi tek bulguda toplanır (CI başına
-- tek bulgu, tek görev). Bulgunun ayrıntısı her değerlendirmede güncellenir; bütün roller kapsayınca bulgu kapanır ve
-- açık görev tetikleyiciyle kapanır (ADR-0037). İzinli türler listesi genişler; görev çalışanına tek bir sütun izni.
BEGIN;

ALTER TABLE insight.quality_rule DROP CONSTRAINT IF EXISTS quality_rule_check_kind_check;
ALTER TABLE insight.quality_rule ADD CONSTRAINT quality_rule_check_kind_check
  CHECK (check_kind IN ('no_relation', 'missing_fields', 'not_seen_by_role', 'matches_condition',
                        'relation_not_allowed', 'coverage_gap'));

-- Görev çalışanı kapsam boşluğunu değerlendirirken kaynağın CI'ı en son ne zaman gördüğünü okur. Yalnızca kimlik ve
-- görülme zamanı sütunları; kaynak kaydının içeriği (attributes, lookup, relations) görülmez, yazma yok.
GRANT SELECT (data_source, source_key, last_seen) ON ingest.source_record TO evren_app_governance;

COMMIT;
