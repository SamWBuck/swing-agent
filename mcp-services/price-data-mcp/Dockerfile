FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /app

RUN npm install -g supergateway

EXPOSE 8810

CMD ["supergateway", "--port", "8810", "--stdio", "price-data-mcp", "--outputTransport", "streamableHttp", "--streamableHttpPath", "/mcp", "--stateful"]
