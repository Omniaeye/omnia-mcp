# Copyright 2026 OMNIA EYE Corporation. SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
LABEL org.opencontainers.image.title="OMNIA MCP — JEV & LAYA"
LABEL org.opencontainers.image.source="https://github.com/Omniaeye/omnia-mcp"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL io.modelcontextprotocol.server.name="io.github.Omniaeye/omnia-mcp"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMNIA_DATABASE=/data/decisions.sqlite3
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels omnia-mcp \
    && useradd --create-home --uid 10001 omnia \
    && mkdir /data \
    && chown omnia:omnia /data
USER omnia
WORKDIR /home/omnia
VOLUME ["/data"]
ENTRYPOINT ["omnia-mcp"]
CMD ["serve"]
