# ─────────────────────────────────────────────
#  Mnogram · Production Dockerfile
#  Target: Azure App Service / Container Apps
# ─────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="mnogram"
LABEL description="Cloud-Native Enterprise Media Platform"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (layer cache optimisation)
COPY requirements.txt .

# Install Python packages (core + Azure SDKs) from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Streamlit configuration
RUN mkdir -p /app/.streamlit
COPY streamlit_config.toml /app/.streamlit/config.toml

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

# Expose port
EXPOSE 8501

# Health check for Azure App Service
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run Streamlit
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]