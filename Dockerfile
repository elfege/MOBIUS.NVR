# Unified NVR Dockerfile
# Supports both Eufy (via Node.js bridge) and UniFi Protect cameras
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    nodejs \
    npm \
    jq \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python requirements first (for better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Node.js dependencies and install
COPY package.json package-lock.json ./
RUN npm ci --production

# Copy application files
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/logs \
    /app/streams \
    /app/config \
    /app/static \
    /app/templates \
    /app/services

# Create non-root user and set ownership
RUN useradd -r -s /bin/false -u 1000 appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose Flask port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/status || exit 1

# Run the application with Flask dev server
# NOTE: Using debug=False and use_reloader=False to prevent double-process issues
CMD ["python3", "app.py"]