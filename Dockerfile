# One image, two roles: the web server (command side + query pages)
# and the projector worker (read side).  See docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Collect CSS/JS once at build time (any DB mode works for this step).
RUN DB_MODE=sqlite VAR_DIR=/tmp/build python manage.py collectstatic --noinput

EXPOSE 8000
