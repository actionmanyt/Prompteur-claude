FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dossier des données (future base SQLite), monté en volume
RUN mkdir -p /data && chown nobody /data

COPY app.py .
COPY static/ ./static/

USER nobody
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]