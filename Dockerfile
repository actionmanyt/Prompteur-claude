FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dossier des données (base SQLite), monté en volume
RUN mkdir -p /data && chown nobody:nogroup /data

COPY *.py ./
COPY static/ ./static/
RUN chmod -R a+rX /app

# Commit affiché sur la page Admin (passé par deploy.sh)
ARG GIT_COMMIT=inconnu
ENV GIT_COMMIT=${GIT_COMMIT} PYTHONUNBUFFERED=1

USER nobody
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
