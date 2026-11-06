FROM python:3.12-slim AS builder

WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml alembic.ini ./
# Dependencies get their own layer (rebuilt only when pyproject.toml changes) by installing
# against a placeholder package first.
RUN mkdir ipalpha && touch ipalpha/__init__.py \
    && pip install --prefix=/install . \
    && rm -rf ipalpha

COPY ipalpha ./ipalpha
RUN pip install --prefix=/install --no-deps --force-reinstall .


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --from=builder /src/alembic.ini ./
# alembic.ini resolves `script_location = ipalpha/alembic` relative to the working directory.
RUN ln -s /usr/local/lib/python3.12/site-packages/ipalpha ipalpha

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=3)"

CMD ["uvicorn", "ipalpha.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
