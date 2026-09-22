FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /pipeline

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY batch_parse.py chart_parser.py enrich_songs.py load_songs.py schema.sql ./

CMD ["python", "--version"]
