FROM stefanoamorelli/sec-edgar-mcp:latest

EXPOSE 9870

# Run with streamable-HTTP transport so the server is reachable over the network.
# SEC_EDGAR_USER_AGENT must be supplied at runtime (see docker-compose.yml / .env).
CMD ["python", "-m", "sec_edgar_mcp.server", "--transport", "streamable-http", "--port", "9870"]
