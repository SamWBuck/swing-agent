FROM python:3.12-slim-bookworm

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastmcp /app

EXPOSE 8810

CMD ["fastmcp", "run", "src/price_data_mcp/server.py:mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8810"]
