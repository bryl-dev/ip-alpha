FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY ipalpha ./ipalpha
RUN pip install --no-cache-dir .

COPY alembic.ini ./

CMD ["uvicorn", "ipalpha.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
