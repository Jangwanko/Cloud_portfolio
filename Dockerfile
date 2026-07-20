FROM python:3.11.15-slim-bookworm@sha256:f5cf0344c9886ff24d34797578d5d7dd6e8911ae0fe5962bb55d0f89603ec361

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /service

COPY requirements.txt .
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY --chown=app:app portfolio ./portfolio
COPY --chown=app:app worker ./worker
COPY --chown=app:app demo ./demo
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini

USER 10001:10001

CMD ["python", "-m", "uvicorn", "portfolio.main:app", "--host", "0.0.0.0", "--port", "8000"]
