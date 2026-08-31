FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHIYUAN_DATA_DIR=/data \
    SHIYUAN_HOST=0.0.0.0 \
    SHIYUAN_PORT=8710

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app /app/app

EXPOSE 8710
CMD ["python", "-m", "app.main"]
