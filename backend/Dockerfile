FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash tippecanoe default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /app/pyproject.toml
COPY backend /app/backend
COPY pipeline /app/pipeline
COPY config /app/config
COPY distance-core /app/distance-core
COPY deploy/render/api-data /app/data/processed

RUN pip install --no-cache-dir fastapi uvicorn pydantic PyYAML

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
