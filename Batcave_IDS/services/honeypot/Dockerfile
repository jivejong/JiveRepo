FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/honeypot/ services/honeypot/
RUN touch services/__init__.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "services.honeypot.app:app", "--host", "0.0.0.0", "--port", "8000"]
