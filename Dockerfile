FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /service

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portfolio ./portfolio
COPY worker ./worker
COPY observer ./observer
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

CMD ["python", "-m", "uvicorn", "portfolio.main:app", "--host", "0.0.0.0", "--port", "8000"]
