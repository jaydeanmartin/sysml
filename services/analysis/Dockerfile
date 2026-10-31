FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY seed ./seed
COPY tests ./tests
COPY conftest.py ./
RUN useradd --create-home --uid 10001 sysml
USER sysml
ENV PYTHONUNBUFFERED=1 SEED_ANALYSES=/app/seed/analyses.json
EXPOSE 8003
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "2"]
