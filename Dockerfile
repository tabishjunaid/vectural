# Backend API image — serves backend.asgi:app with uvicorn.
FROM python:3.12-slim

WORKDIR /app

# git is used by the freshness webhook path (git_name_status); harmless otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching). The real-adapter extras
# (opensearch/neo4j/postgres) plus `temporal` are installed so the one image can
# serve the API over the real stores AND host the durable indexing worker (§5.7) —
# each is just a different Python entrypoint, not a separate build.
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY sample-estate ./sample-estate
# `embeddings` adds real BGE-M3 (sentence-transformers + torch) so the image can
# embed semantically; the ~2 GB model itself is NOT baked in — it downloads at first
# use into a mounted HuggingFace cache volume (see docker-compose `hf-cache`).
RUN pip install --no-cache-dir ".[temporal,opensearch,neo4j,postgres,embeddings]"

ENV VECTURAL_ESTATE_ROOT=sample-estate \
    VECTURAL_MANIFEST_PATH=sample-estate/manifest.yaml \
    VECTURAL_CORS_ORIGINS='["http://localhost:5175"]'

EXPOSE 8000
CMD ["uvicorn", "backend.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
