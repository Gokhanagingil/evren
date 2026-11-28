-- 0039 — Kapsam boşluğu mu, kimlik boşluğu mu? (P1.2, F1-30, ADR-0046)
--
-- Kapsam kuralı, eksik rolün kaynağında bu CI'ı aday gösteren açık bir eşleştirme kuyruğu kaydı olup olmadığına bakar:
-- varsa kaynak sunucuyu büyük olasılıkla tanıyordur, sorun eşleştirmededir ve sahibine görev açılmaz. Görev çalışanı
-- bunun için kuyruk kaydının kimliğini, kaynağını, durumunu ve aday CI'larını okur; kaynak kaydının içeriğini ve karar
-- sütunlarını göremez, yazamaz (0033'teki status okumasına ek).
BEGIN;

GRANT SELECT (sys_id, data_source, status, candidate_ci, candidates) ON ingest.match_candidate TO evren_app_governance;

COMMIT;
