# One image, two Railway services (SPEC.md sections 2.2 and 3, Phase 2
# session 4). SERVICE_ROLE picks the process - see serving/entrypoint.py.
FROM python:3.11-slim

# PYTHONUNBUFFERED because Python buffers stdout when it is not a TTY, and
# every log line in this codebase is a bare print(). Without it a Railway
# worker looks silent for minutes at a time, which is indistinguishable from
# hung. PYTHONDONTWRITEBYTECODE keeps the layer clean.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_CACHE_DIR=/app/artifacts

# libgomp1 is LightGBM's OpenMP runtime. The wheel links against it and fails
# at import, not at install, so leaving it out produces a container that
# builds cleanly and dies on the first prediction.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first: pyproject alone, so a source change does not
# reinstall lightgbm/scikit-learn on every build.
COPY pyproject.toml ./
RUN mkdir -p src && python -m pip install --upgrade pip && pip install .

COPY src/ ./src/
RUN pip install --no-deps .

# The artifact cache is a real directory so a restart can reuse a verified
# download instead of refetching (models/artifact.py).
RUN mkdir -p /app/artifacts

# Non-root: nothing here needs to write outside /app/artifacts.
RUN useradd --create-home --uid 10001 sightscreen \
 && chown -R sightscreen:sightscreen /app
USER sightscreen

# WORKDIR is /app and there is no .env here, by design. config.py's
# env_file=".env" therefore finds nothing and every setting comes from the
# real environment - which is what Railway provides. If an api/.env ever
# leaked into the image it would carry LOCAL_DATABASE_URL, and the section
# 2.1 boundary check would refuse to start. That is the intended outcome.
EXPOSE 8000
CMD ["python", "-m", "serving.entrypoint"]
