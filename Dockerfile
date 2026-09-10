FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    DENO_INSTALL="/usr/local" \
    PATH="/usr/local/bin:${PATH}"

# Install FFmpeg, Node.js, curl, unzip and system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Deno to /usr/local/bin so it is globally available in PATH
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y --dir /usr/local && \
    chmod +x /usr/local/bin/deno && \
    deno --version && \
    ffmpeg -version

WORKDIR /app

# Install Python requirements with complete yt-dlp[default] and yt-dlp-ejs
COPY requirements.txt .
RUN pip install --no-cache-dir -U "yt-dlp[default]" yt-dlp-ejs && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure downloads and logs directories exist
RUN mkdir -p downloads logs

EXPOSE 5000

# Start Uvicorn bound to 0.0.0.0 and dynamic $PORT
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-5000}"]
