FROM python:3.12-slim-bookworm

WORKDIR /app

COPY database/pyproject.toml /app/database/pyproject.toml
COPY database/src /app/database/src
COPY mcp-services/price-data-mcp/pyproject.toml /app/price-data-mcp/pyproject.toml
COPY mcp-services/price-data-mcp/src /app/price-data-mcp/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastmcp /app/database /app/price-data-mcp

EXPOSE 8810

CMD ["fastmcp", "run", "/app/price-data-mcp/src/price_data_mcp/server.py:mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8810"]
