FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Europe/Madrid

WORKDIR /app
COPY . /app
RUN mkdir -p /app/data

# El bot usa solo la biblioteca estándar: no hay pip install.
CMD ["python", "-u", "run.py", "watch"]
