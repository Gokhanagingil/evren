# Evren CMDB — tek imaj, birden çok servis (api, worker, migrate, demo; U2'de gateway, U3'te indexer). ADR-0016.
#   docker build -f deploy/Dockerfile -t evren:dev .
# Güvenlik: bağımlılıklar hash'li kilitten (--require-hashes), root olmayan kullanıcı, kaynak dosyalar salt
# okunur (root sahipli), derleme araçları son katmanda yok. Temel görüntü özetiyle (digest) sabitlenir.
# python:3.12-slim çok mimarili imaj dizini (25.11.2026). Güncelleme: tools/dev_stack.py digests; tek değişiklik olarak.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements/runtime.txt /tmp/runtime.txt
# İsteğe bağlı derleme sırrı "build_ca": TLS denetimli kurumsal vekil sunucunun CA paketi. Yalnızca bu adımda
# bağlanır, imaj katmanına ve geçmişine yazılmaz (tools/dev_stack.py: EVREN_BUILD_CA_FILE).
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/runtime.txt

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="Evren CMDB" \
      org.opencontainers.image.description="Evren CMDB servisleri: api, worker, migrate" \
      org.opencontainers.image.source="https://github.com/Gokhanagingil/evren"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVREN_PORT=8088
# Debian güvenlik güncellemeleri derleme anında uygulanır: temel görüntü yeniden yayınlanmadan düzeltilen CVE'ler
# imaja girmez (CI'da Trivy imaj kapısı, düzeltmesi olan YÜKSEK/KRİTİK bulguda durur). Depoya erişilemezse derleme
# durur (--error-on=any; apt aksi hâlde sessizce eski listeyle devam eder). OS_UPDATES=0 yalnızca ağsız geliştirme
# derlemesi içindir; CI ve üretim derlemesi varsayılanı (1) kullanır.
ARG OS_UPDATES=1
RUN if [ "$OS_UPDATES" = "1" ]; then \
      apt-get update --error-on=any \
      && apt-get -y upgrade --no-install-recommends \
      && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb; \
    else echo "UYARI: OS_UPDATES=0 — Debian güvenlik güncellemeleri uygulanmadı (yalnızca geliştirme)"; fi \
 && useradd --system --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin evren
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY packages/evren-core/src packages/evren-core/src
COPY services/api/evren_api services/api/evren_api
COPY services/reconciler/evren_reconciler services/reconciler/evren_reconciler
COPY tools tools
COPY metadata metadata
COPY db/migrations db/migrations
COPY connectors connectors
COPY schemas schemas
COPY apps/web apps/web
COPY deploy/evren.py deploy/evren.py
USER 10001
EXPOSE 8088
ENTRYPOINT ["python", "/app/deploy/evren.py"]
CMD ["api"]
