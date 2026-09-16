FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/consumer/ services/consumer/
COPY services/common/ services/common/
RUN touch services/__init__.py

ENV PATH="/app/.venv/bin:$PATH"

# Without this, Python buffers stdout when it isn't a TTY and `docker compose
# logs` shows nothing until the buffer fills — which would hide the flush and
# commit lines that the restart exercise reads to see where the batch stopped.
ENV PYTHONUNBUFFERED=1

# ./data is bind-mounted here (docker-compose.yml) so landed Parquet appears on
# the host for DuckDB and dbt without a copy step (docs/05).
ENV DATA_ROOT=/app/data

CMD ["python", "-m", "services.consumer"]
