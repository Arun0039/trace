FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy entire repo
COPY . /app

# Create qa-platform directory
RUN mkdir -p /app/qa-platform/storage

WORKDIR /app/backend

# Expose port - Render will override via PORT
EXPOSE 8001

# Default env (can be overridden)
ENV HOST=0.0.0.0
ENV PORT=8001
ENV QA_HOST=0.0.0.0
ENV QA_PORT=8001

CMD ["python", "app.py"]
