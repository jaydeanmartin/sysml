# Build context: repository root (so the example models can be baked in)
FROM python:3.12-slim
WORKDIR /app
COPY services/model/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/model/app ./app
COPY services/model/tests ./tests
COPY services/model/conftest.py ./
COPY examples ./examples
RUN useradd --create-home --uid 10001 sysml
USER sysml
ENV PYTHONUNBUFFERED=1 EXAMPLES_DIR=/app/examples
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
