FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_WATCH_CONFIG=/app/data/config.json \
    TZ=Asia/Shanghai

RUN pip install --no-cache-dir flask zstandard

COPY aw/ ./aw/
COPY static/ ./static/
COPY hub.py collector.py scan_once.py config.example.json ./

RUN mkdir -p /app/data

EXPOSE 8090
CMD ["python", "hub.py"]
