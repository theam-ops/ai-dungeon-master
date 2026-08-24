FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY game/ ./game/
COPY static/ ./static/
COPY server.py dnd.py ./

# Render/Railway/Fly all inject $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
