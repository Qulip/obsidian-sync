FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini main.py ./
COPY alembic ./alembic
COPY src ./src
COPY docker-entrypoint.sh ./

RUN uv sync --frozen --no-dev \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uv", "run", "--frozen", "uvicorn", "obsidian_sync.app:app", "--host", "0.0.0.0", "--port", "8000"]
