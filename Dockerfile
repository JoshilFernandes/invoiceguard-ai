FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
ARG EXTRAS=groq
RUN pip install ".[${EXTRAS}]"

COPY data ./data
ENV INVOICEGUARD_DATA_DIR=/app/data INVOICEGUARD_STATE_DIR=/var/lib/invoiceguard
RUN useradd --create-home app && mkdir -p /var/lib/invoiceguard && chown app /var/lib/invoiceguard
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "invoiceguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
