FROM node:20-bookworm-slim

# Install Python 3.11 (ships with Debian Bookworm) and venv
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual env so pip doesn't fight with the system packages
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the yahoo-finance MCP server
RUN pip install --no-cache-dir yahoo-finance-server

# Install supergateway — bridges stdio ↔ SSE so the server is reachable over HTTP
RUN npm install -g supergateway

EXPOSE 8808

# Exposes the MCP server at  http://<host>:8808/sse
CMD ["supergateway", "--port", "8808", "--stdio", "yahoo-finance-server"]
