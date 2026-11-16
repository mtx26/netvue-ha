FROM python:3.13.5-slim-bookworm@sha256:a7cb177dc60dfa77b223192cd0d9e6ce1aac7cf9a569023d68e7c91b6371a718

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY netvue_bridge/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt av==16.1.0 \
    && python -m pip check
COPY netvue_bridge/ /app/netvue_bridge/

USER 10001:10001
ENTRYPOINT ["python", "-m", "netvue_bridge.continuous"]
CMD ["run"]
