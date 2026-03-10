FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/Alex2Yang97/yahoo-finance-mcp.git /app

WORKDIR /app

RUN uv venv /app/.venv \
	&& uv pip install --python /app/.venv/bin/python -e .

RUN uv add fastmcp

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8809

CMD ["uv", "run", "fastmcp", "run", "server.py:yfinance_server", "--transport", "http", "--host", "0.0.0.0", "--port", "8809"]