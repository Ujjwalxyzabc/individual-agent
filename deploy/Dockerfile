

FROM python:3.11-slim

# Install OS dependencies for pandas, openpyxl, xlrd, msal, requests, guardrails, azure-ai-contentsafety
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
        libjpeg-dev \
        libpq-dev \
        gcc \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# Create app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port for FastAPI
EXPOSE 8000

# Set default log file location (can be overridden by env)
ENV AUDIT_LOG_PATH=/app/audit.log

# Startup command
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8000"]

