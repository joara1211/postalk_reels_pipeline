FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home renderer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY templates ./templates
COPY assets ./assets
USER renderer
CMD ["sh", "-c", "test -n \"$REELS_INTERNAL_API_KEY\" && test -n \"$SUPABASE_URL\" && exec python -m uvicorn src.quote_video.api:app --host :: --port ${PORT:-8000}"]
