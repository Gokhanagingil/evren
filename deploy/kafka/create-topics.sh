#!/bin/bash
# Kafka konularını oluşturur (varsa dokunmaz). Kullanım: create-topics.sh <bootstrap>
set -euo pipefail
BOOT="${1:?bootstrap sunucusu gerekli}"
grep -v '^#' /topics/topics.txt | while read -r name parts retention; do
  [ -z "${name:-}" ] && continue
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOT" --create --if-not-exists \
    --topic "$name" --partitions "$parts" --replication-factor 1 --config "retention.ms=$retention"
done
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOT" --list
