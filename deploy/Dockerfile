# The Python image: the Brain API, ingestion and the two simulators all run from it (deploy/docker-compose.yml).
# Build context: the repo root.   docker build -f deploy/Dockerfile -t internal-brain-app .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models
WORKDIR /app

# WITH_MODELS=1 adds CPU-only torch and sentence-transformers (bge-m3 and the layer 2 checker), about 1.5 GB more.
# WITH_MODELS=0 is for a small machine or a smoke test: run with EMBEDDING_BACKEND=none and CHECKER_MODEL=none.
ARG WITH_MODELS=1
COPY requirements-dev.txt requirements-embed.txt ./
RUN pip install -r requirements-dev.txt \
 && if [ "$WITH_MODELS" = "1" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install -r requirements-embed.txt; \
    fi

COPY pyproject.toml ./
COPY brain ./brain
COPY connectors ./connectors
COPY simulators ./simulators
COPY mcp_server ./mcp_server
COPY fixtures ./fixtures
COPY evals ./evals
COPY db ./db
COPY deploy/entry.py ./deploy/entry.py

# Not root. /models holds the model cache, /state what must outlive a container, evals/scoreboard the Leak-CI result.
RUN useradd --uid 10001 --create-home app \
 && mkdir -p /models /state /app/evals/scoreboard \
 && chown -R app:app /models /state /app/evals/scoreboard /app/connectors/gdrive
USER app

ENTRYPOINT ["python", "deploy/entry.py"]
CMD ["brain"]
