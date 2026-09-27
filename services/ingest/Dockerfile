FROM python:3.11-slim

# tini as PID 1: a bare python PID 1 ignores SIGTERM (the kernel drops
# default-action signals to PID 1), so every deploy/restart ended in
# SIGKILL after the grace period ("exited with status 137").
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user
RUN adduser --disabled-password --no-create-home appuser \
    && mkdir -p /app/data && chown appuser /app/data
USER appuser

STOPSIGNAL SIGTERM

ENV DATA_DIR=/app/data
ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py"]
