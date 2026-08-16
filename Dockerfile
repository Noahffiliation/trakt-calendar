FROM python:3.12.3-slim-bookworm

RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py generate_ical.py google_sync.py ical_builder.py trakt_api.py ./

RUN mkdir -p /data && chown -R appuser:appuser /data

ENV DATA_DIR=/data
WORKDIR /data

USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/generate_ical.py"]
