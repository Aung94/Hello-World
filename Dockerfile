FROM python:3.12-slim

# ffmpeg for rendering, Noto fonts so Burmese (Myanmar script) subtitles render correctly
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-ui-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV HOST=0.0.0.0 PORT=8000 NO_BROWSER=1 JOBS_DIR=/data/jobs
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "app"]
