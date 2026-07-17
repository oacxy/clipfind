# ClipFind needs the ffmpeg binary on the system (for trimming clips), which
# Render's plain "Python 3" native runtime does not include. Deploying via
# this Dockerfile instead guarantees ffmpeg + yt-dlp are both present.

FROM python:3.11-slim

# ffmpeg is a system package, not a pip package — install it via apt.
# fonts-dejavu-core is needed too: burned-in captions render via ffmpeg's
# libass-based `subtitles` filter, which needs an actual font file on disk
# to draw text with — without one, caption text silently fails to render.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directory where cut clips get written before being served back.
RUN mkdir -p /app/clips_output

EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "300"]
