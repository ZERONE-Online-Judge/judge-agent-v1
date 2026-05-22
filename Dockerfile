FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc g++ default-jdk-headless isolate \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app ./app
CMD ["python", "-m", "app.main"]
