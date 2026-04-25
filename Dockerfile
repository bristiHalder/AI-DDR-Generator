FROM python:3.11-slim

# System dependencies for PDF processing (Playwright headless browser)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its Chromium dependencies
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy all source files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create necessary directories
RUN mkdir -p backend/uploads backend/outputs

WORKDIR /app/backend

# Expose port (Railway / Render use PORT env var)
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
