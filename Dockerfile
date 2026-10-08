FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# requirements-prod.txt is requirements.txt plus the Postgres driver, which DND_MODE=prod
# needs and the default mode never imports
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY game/ ./game/
COPY static/ ./static/
# the SRD the DM looks rules up in - without it, lookup_rule is quietly never offered
COPY data/ ./data/
COPY server.py dnd.py ./

# Render/Railway/Fly all inject $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
