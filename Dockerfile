FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . pytest

# Agents work (and run generated code) inside /workspace. Mount a folder there.
WORKDIR /workspace
ENTRYPOINT ["coding-agents", "--workspace", "/workspace"]
