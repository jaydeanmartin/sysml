FROM python:3.12-slim AS shim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY docker/nofpe.c /src/nofpe.c
RUN gcc -shared -fPIC -O2 -o /src/nofpe.so /src/nofpe.c

FROM python:3.12-slim
# XFOIL (Drela, MIT; GPL) from Debian, run headless in batch mode
RUN apt-get update && apt-get install -y --no-install-recommends xfoil && rm -rf /var/lib/apt/lists/*
COPY --from=shim /src/nofpe.so /opt/xfoil/nofpe.so
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
COPY conftest.py ./
RUN useradd --create-home --uid 10001 sysml
USER sysml
ENV PYTHONUNBUFFERED=1
EXPOSE 8012
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012", "--workers", "2"]
