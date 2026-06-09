FROM python:3.11-slim

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
CMD ["python", "main.py"]
