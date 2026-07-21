FROM python:3.13-alpine

RUN apk add --no-cache docker-cli docker-cli-compose

WORKDIR /app
COPY updater/updater.py ./updater.py

ENV UPDATER_PORT=3188 \
    UPDATER_DATA_DIR=/data/update \
    UPDATER_PROJECT_DIR=/project \
    UPDATER_CONTAINER_NAME=watering-planner-updater

EXPOSE 3188

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:3188/health', timeout=3).read()"

CMD ["python", "updater.py"]
