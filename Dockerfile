FROM python:3.12.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The meaning-match model (about 220 MB) is baked into the image, so the bot never downloads at
# runtime and the container can keep a read-only filesystem.
ENV FASTEMBED_CACHE_PATH=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/models')" \
    && chmod -R a+rX /app/models
ENV HF_HUB_OFFLINE=1

COPY zyroguard/ ./zyroguard/

RUN useradd --system --uid 10001 --no-create-home zyroguard \
    && mkdir -p /data && chown zyroguard:zyroguard /data
USER zyroguard

VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "zyroguard.main"]
