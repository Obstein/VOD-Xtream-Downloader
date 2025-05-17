FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y wget && \
    pip install flask requests && \
    apt-get clean

WORKDIR /app
COPY . /app

CMD ["python", "app.py"]
