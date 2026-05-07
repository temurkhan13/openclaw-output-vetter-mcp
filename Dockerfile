# Dockerfile — openclaw-output-vetter-mcp
#
# Build: docker build -t openclaw-output-vetter-mcp .
# Run:   docker run -i openclaw-output-vetter-mcp
#
# The MCP server speaks stdio JSON-RPC. Pipe MCP messages on stdin; receive responses on stdout.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["openclaw-output-vetter-mcp"]
