# WaveFlow server — ONNX engine on an NVIDIA GPU (CUDA). fp32 model (~2.4 GB VRAM).
# The CUDA 12 + cuDNN runtime comes from pip wheels; the NVIDIA Container Toolkit supplies the driver.
# cuDNN is pinned to 9.1.0.70: newer 9.x fails convolutions on Pascal (GTX 10xx) GPUs.
#
# Build from the repo root:
#   docker build -f docker/onnx-gpu/Dockerfile -t waveflow-onnx-gpu .

FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir "onnxruntime-gpu[cuda,cudnn]==1.23.2" nvidia-cudnn-cu12==9.1.0.70 \
        onnx-asr==0.12.0 huggingface_hub fastapi uvicorn python-multipart websockets numpy webrtcvad-wheels
ENV PYTHONUNBUFFERED=1 HF_HOME=/models QUANT=fp32 GPU_MEM_MB=4096
COPY server/parakeet_server.py server/itn.py server/vocab.py /app/
WORKDIR /app
VOLUME /models
EXPOSE 8756
# 0.0.0.0 is inside the container; what is reachable is decided by `-p`. The server refuses
# to start without WAVEFLOW_TOKEN (see docker/compose.yml).
ENTRYPOINT ["sh", "-c", "exec python3 parakeet_server.py --engine onnx --onnx-quant ${QUANT} --device cuda --gpu-mem-mb ${GPU_MEM_MB} --host 0.0.0.0 --port 8756 \"$@\"", "--"]
