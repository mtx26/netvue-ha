FROM python:3.12-slim-bookworm AS model
ENV TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgl1 libglib2.0-0 \
    && pip install --no-cache-dir ultralytics==8.2.0 onnx onnxscript \
    && curl -fL --retry 3 https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt \
      -o /yolov8n.pt \
    && yolo export model=/yolov8n.pt format=onnx imgsz=640 opset=12 \
    && rm -rf /root/.cache /yolov8n.pt

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY netvue_ai.py .
COPY --from=model /yolov8n.onnx /app/yolov8n.onnx

RUN useradd --uid 10001 --create-home ai \
    && mkdir -p /data \
    && chown -R ai:ai /app /data
USER 10001:10001

EXPOSE 8766
STOPSIGNAL SIGTERM
CMD ["python", "-u", "netvue_ai.py"]
