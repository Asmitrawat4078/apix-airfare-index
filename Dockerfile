FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY collector ./collector
COPY index ./index
COPY api ./api
COPY dashboard ./dashboard
COPY scripts ./scripts
COPY tests ./tests
COPY db ./db
COPY docs ./docs

RUN pip install --upgrade pip && pip install -e ".[api,dashboard]"

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
