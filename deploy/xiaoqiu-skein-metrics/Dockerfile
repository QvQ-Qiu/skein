ARG BASE_IMAGE=xiaoqiu:1.0.96-sso
FROM ${BASE_IMAGE}

COPY app/main.py /srv/sse-agent/app/main.py
COPY app/config.py /srv/sse-agent/app/config.py
COPY app/ops/alerts.py /srv/sse-agent/app/ops/alerts.py
COPY app/ops/skein_metrics.py /srv/sse-agent/app/ops/skein_metrics.py
COPY config/settings.yaml /srv/sse-agent/config/settings.yaml
