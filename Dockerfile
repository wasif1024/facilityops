# FacilityOps AI backend image: the Connect RPC API served by uvicorn.
#
# Used by compose.yaml for local setup, and the same image is what you would deploy
# to Azure (Container Apps or AKS). Build: docker build -t facilityops-api .

FROM python:3.14-slim

# No .pyc files, unbuffered logs (so `docker compose logs` shows them immediately),
# and a smaller image without pip's download cache.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies before copying the code: Docker caches this layer, so editing
# code does not reinstall everything. Runtime dependencies only (no tests, no codegen).
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code, migrations and the eval runner (admins can start eval runs).
COPY alembic.ini ./
COPY backend ./backend
COPY evals ./evals

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# Lets Docker (and compose's depends_on) know when the API is actually ready.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

# Factory mode: uvicorn calls create_app(); importing the module alone configures nothing.
CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
