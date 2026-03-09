FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y git python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/Alex2Yang97/yahoo-finance-mcp.git /app

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

RUN pip install --no-cache-dir -e /app

RUN printf '#!/bin/sh\nexec /opt/venv/bin/python /app/server.py\n' > /usr/local/bin/yahoo-finance-mcp \
    && chmod +x /usr/local/bin/yahoo-finance-mcp

RUN npm install -g supergateway

WORKDIR /app

EXPOSE 8809

CMD ["supergateway", "--port", "8809", "--stdio", "yahoo-finance-mcp", "--outputTransport", "streamableHttp", "--streamableHttpPath", "/mcp", "--stateful"]