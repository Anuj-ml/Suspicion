FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUSPICION_DB_PATH=/data/suspicion.db

# Tesseract enables screenshot OCR; PDF text needs no system package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --system --create-home suspicion \
    && mkdir -p /data \
    && chown -R suspicion /data
USER suspicion

VOLUME ["/data"]
EXPOSE 8000

# Long polling: no inbound traffic needed for Telegram. The HTTP port is only
# for /health, /check and the share card. Render/Heroku-style hosts inject $PORT.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
