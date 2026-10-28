FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so this layer is cached across source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src/ ./src/
RUN uv sync --frozen
COPY dags/ ./dags/
COPY monitors/ ./monitors/
COPY oncall/ ./oncall/
COPY eval/ ./eval/

RUN useradd --create-home --uid 1000 rca \
    && mkdir -p /models /app/eval/reports && chown rca /models /app/eval/reports
USER rca

# Placeholder until the API and worker exist (Phase 8).
CMD ["sleep", "infinity"]
