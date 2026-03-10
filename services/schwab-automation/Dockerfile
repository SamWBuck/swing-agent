FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY database /app/database
COPY services/schwab-automation/pyproject.toml /app/automation/pyproject.toml
COPY services/schwab-automation/src /app/automation/src
COPY services/schwab-automation/cron /app/cron
COPY token.json /app/token.json

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /app/database \
    && pip install --no-cache-dir /app/automation

RUN sed -i 's/\r$//' /app/cron/entrypoint.sh /app/cron/run_hourly.sh /app/cron/schwab-automation.cron \
    && printf '\n' >> /app/cron/schwab-automation.cron \
    && chmod +x /app/cron/entrypoint.sh /app/cron/run_hourly.sh \
    && cp /app/cron/schwab-automation.cron /etc/cron.d/schwab-automation \
    && chmod 0644 /etc/cron.d/schwab-automation

ENTRYPOINT ["/app/cron/entrypoint.sh"]
CMD ["cron", "-f"]