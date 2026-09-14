# WaveFlow server — ONNX engine on CPU (default engine). No GPU needed. Image ~1 GB;
# the int8 model (~660 MB) downloads on first start into the /models volume.
#
# Build from the repo root:
#   docker build -f docker/onnx/Dockerfile -t waveflow-onnx .
# Set THREADS to the machine's PHYSICAL performance cores (never hyperthreads or E-cores).

FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir onnx-asr==0.12.0 onnxruntime==1.30.0 huggingface_hub \
        fastapi uvicorn python-multipart websockets numpy webrtcvad-wheels
ENV PYTHONUNBUFFERED=1 HF_HOME=/models THREADS=4
COPY server/parakeet_server.py server/itn.py server/vocab.py /app/
WORKDIR /app
VOLUME /models
EXPOSE 8756
# 0.0.0.0 is inside the container; what is reachable is decided by `-p`. The server refuses
# to start without WAVEFLOW_TOKEN (see docker/compose.yml).
ENTRYPOINT ["sh", "-c", "exec python3 parakeet_server.py --engine onnx --onnx-quant int8 --device cpu --threads ${THREADS} --host 0.0.0.0 --port 8756 \"$@\"", "--"]
