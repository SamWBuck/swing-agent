FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY services/schwab-price-sync/pyproject.toml /app/pyproject.toml
COPY services/schwab-price-sync/src /app/src
COPY database/pyproject.toml /app/database/pyproject.toml
COPY database/src /app/database/src
COPY services/schwab-price-sync/cron /app/cron
COPY token.json /app/token.json

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /app/database /app

RUN sed -i 's/\r$//' /app/cron/entrypoint.sh /app/cron/run_missing_sync.sh /app/cron/run_frequent_sync.sh /app/cron/run_hourly_sync.sh /app/cron/schwab-price-sync.cron \
    && printf '\n' >> /app/cron/schwab-price-sync.cron \
    && chmod +x /app/cron/entrypoint.sh /app/cron/run_missing_sync.sh /app/cron/run_frequent_sync.sh /app/cron/run_hourly_sync.sh \
    && cp /app/cron/schwab-price-sync.cron /etc/cron.d/schwab-price-sync \
    && chmod 0644 /etc/cron.d/schwab-price-sync

ENTRYPOINT ["/app/cron/entrypoint.sh"]
CMD ["cron", "-f"]