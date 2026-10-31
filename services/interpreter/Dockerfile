# Build context: repository root (the tests use the example model)
FROM python:3.12-slim

# OpenMBEE OpenSysML: the SysML v2 / KerML interpreter (static Go binaries)
ARG OPENSYSML_VERSION=v0.11.0
ARG TARGETARCH=amd64
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl z3 \
 && curl -fsSL "https://github.com/Open-MBEE/OpenSysML/releases/download/${OPENSYSML_VERSION}/opensysml-linux-${TARGETARCH}.tar.gz" \
    | tar xz -C /usr/local/bin sysml sysml-lsp \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && sysml -version

WORKDIR /app
COPY services/interpreter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/interpreter/app ./app
COPY services/interpreter/tests ./tests
COPY services/interpreter/conftest.py ./
COPY examples ./examples
RUN chmod 755 app/bridge.py && useradd --create-home --uid 10001 sysml
USER sysml

ENV OPENSYSML_BIN=sysml \
    SYSML_TOOLS_BASE=/tmp/sysml-tools \
    OPENSYSML_TOOL_TIMEOUT=60s \
    PYTHONUNBUFFERED=1
EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
