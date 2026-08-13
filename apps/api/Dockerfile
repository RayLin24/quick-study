FROM ghcr.io/astral-sh/uv:0.9.26 AS uv

FROM python:3.12-slim-bookworm

ENV ARTIFACTS_DIR=/app/data/artifacts \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY apps/api/app ./app
# Migrations ship with the image so an operator can run `alembic upgrade head` in the
# container that already has the right dependencies.
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/alembic ./alembic

# Dependencies and source stay root-owned and read-only; only the artifact root, which is the
# Compose bind-mount target shared with the worker, is writable by the unprivileged user.
RUN mkdir -p "$ARTIFACTS_DIR" \
    && chown -R app:app /app/data \
    && chmod 0750 /app/data "$ARTIFACTS_DIR"

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000"]
