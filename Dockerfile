# Backend API image — serves backend.asgi:app with uvicorn.
FROM python:3.12-slim

WORKDIR /app

# git is used by the freshness webhook path (git_name_status); harmless otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY sample-estate ./sample-estate
RUN pip install --no-cache-dir .

ENV VECTURAL_ESTATE_ROOT=sample-estate \
    VECTURAL_MANIFEST_PATH=sample-estate/manifest.yaml \
    VECTURAL_CORS_ORIGINS='["http://localhost:5175"]'

EXPOSE 8000
CMD ["uvicorn", "backend.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
