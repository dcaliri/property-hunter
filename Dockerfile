FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY docker-compose.cloud.yml /app/docker-compose.cloud.yml
COPY Caddyfile /app/Caddyfile

RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir sqlite-web

RUN mkdir -p /app/data

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

VOLUME ["/app/data"]

ENV DB_PATH=/app/data/property_hunter.db

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["scheduler"]
