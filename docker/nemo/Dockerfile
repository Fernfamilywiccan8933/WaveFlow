# WaveFlow server — NeMo engine ("max quality"). NVIDIA GPU + NVIDIA Container Toolkit.
# Python 3.11.9, torch 2.6.0+cu124 (bundles CUDA/cuDNN 9.1, works back to Pascal/GTX 10xx),
# NeMo 2.7.3, every package pinned to a known-good set (pin.py + constraints.txt).
# fp16 model, ~1.6 GB VRAM. Image ~7 GB unpacked.
#
# Build from the repo root:
#   docker build -f docker/nemo/Dockerfile -t waveflow-nemo .

FROM python:3.11.9-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir pip==24.0
RUN pip install --no-cache-dir torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 \
        --index-url https://download.pytorch.org/whl/cu124
RUN pip install --no-cache-dir "nemo-toolkit[asr]==2.7.3" \
        fastapi uvicorn python-multipart websockets "setuptools<81" webrtcvad-wheels
COPY docker/nemo/constraints.txt docker/nemo/pin.py /tmp/
RUN python /tmp/pin.py
# fail the build if NeMo cannot import — never ship a half-installed image
RUN python -c "import nemo.collections.asr, torch, webrtcvad; print('IMPORT_OK', torch.__version__)"

FROM python:3.11.9-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
COPY server/parakeet_server.py server/itn.py server/vocab.py /app/
WORKDIR /app
EXPOSE 8756
# 0.0.0.0 is inside the container; what is reachable is decided by `-p`. The server refuses
# to start without WAVEFLOW_TOKEN (see docker/compose.yml).
ENTRYPOINT ["python3", "parakeet_server.py", "--engine", "nemo", "--host", "0.0.0.0", "--port", "8756"]
