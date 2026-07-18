FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVAGENT_WORKSPACE=/workspace

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/devagent-web-runtime \
    && cd /opt/devagent-web-runtime \
    && npm init -y \
    && npm install --ignore-scripts --no-audit --no-fund vite@8.1.4 react@19.2.7 react-dom@19.2.7
RUN pip install --no-cache-dir -r requirements.txt

COPY devagent ./devagent
COPY tests ./tests

EXPOSE 5000

CMD ["python", "-m", "devagent"]
