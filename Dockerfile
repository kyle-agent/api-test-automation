# SCP API Regression Test Platform — one image for both compose services
# (server: uvicorn controlplane.app · worker: python -m runner.worker).
# docs/PLATFORM-PLAN.md §1 원칙 5: 호스트 불문 동일 배포. The repo working copy
# is bind-mounted over /app by docker-compose.yml (it IS the shared source of
# truth — UI edits apply to the next run immediately); the COPY below only
# keeps the image self-contained for standalone `docker run`.
FROM python:3.12-slim

# git: authoring commits (controlplane/authoring.py) + run revision stamping
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# layer-cache the dependency install (renamed: the basenames collide)
COPY requirements.txt /tmp/req/engine.txt
COPY controlplane/requirements.txt /tmp/req/controlplane.txt
COPY controlplane/requirements-ai.txt /tmp/req/ai.txt
RUN pip install --no-cache-dir -r /tmp/req/engine.txt -r /tmp/req/controlplane.txt
# AI triage/pipelines are optional — uncomment to bake them in:
# RUN pip install --no-cache-dir -r /tmp/req/ai.txt

COPY . /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# the bind-mounted working copy must be a safe git dir for authoring commits
RUN git config --global --add safe.directory /app

EXPOSE 8800
CMD ["uvicorn", "controlplane.app:app", "--host", "0.0.0.0", "--port", "8800"]
