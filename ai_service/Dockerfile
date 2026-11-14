FROM python:3.12-slim

WORKDIR /app

# Torch CPU wheels keep the image smaller than the default CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# BAAI/bge-m3 (~2 GB) is downloaded here on first start; the compose file
# mounts a named volume at this path so it survives container rebuilds.
ENV HF_HOME=/root/.cache/huggingface

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
