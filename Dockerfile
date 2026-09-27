FROM cgr.dev/chainguard/python:latest-dev@sha256:af8fafef8f22e0f7004332dd8ff0a247e273ec159fd8919dfeb3c6fb6142d3b9 AS builder

WORKDIR /app

COPY pyproject.toml .
COPY src ./src
COPY scripts/qualify_models.py ./qualify_models.py
COPY config/pilot.yaml ./pilot.yaml

USER 0:0
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir '.[phase1]'

FROM cgr.dev/chainguard/python:latest@sha256:1dc2f617fc4430893004717cace9029b854e1dfb78b3a71220be6f2eb20a4c7f

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/qualify_models.py ./qualify_models.py
COPY --from=builder /app/pilot.yaml ./pilot.yaml

ENV FACTORY_API_HOST=0.0.0.0 \
    FACTORY_API_PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:/usr/bin"

USER 65532:65532

EXPOSE 8080

ENTRYPOINT ["/opt/venv/bin/factory-api"]
