FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DATA_DIR=/app/data

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY server.py README.md ./
COPY public ./public

RUN mkdir -p /app/data && chown -R appuser:appuser /app

EXPOSE 8080
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/api/state', timeout=3).read()"

USER appuser

CMD ["python", "server.py"]
