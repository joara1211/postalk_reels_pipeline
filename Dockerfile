# quote-video pipeline API — see src/quote_video/api.py.
# Bakes in ffmpeg + the subtitle font (the only asset the pipeline itself
# needs at runtime); source clips/BGM arrive per-request as URLs, so
# assets/videos and assets/bgm are NOT baked in here (see .dockerignore).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY assets/font/ assets/font/

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Cloud Run injects $PORT; default to 8080 for local `docker run`.
CMD exec uvicorn src.quote_video.api:app --host 0.0.0.0 --port ${PORT:-8080}
