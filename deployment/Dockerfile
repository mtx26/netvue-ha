FROM python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY netvue_bridge/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt av==16.1.0 \
    && python -m pip check
COPY netvue_bridge/ /app/netvue_bridge/

USER 10001:10001
ENTRYPOINT ["python", "-m", "netvue_bridge.manager"]
