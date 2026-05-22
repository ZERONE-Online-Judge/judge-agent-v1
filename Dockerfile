FROM python:3.13-slim

RUN apt-get update \

    && apt-get install -y --no-install-recommends \

       gcc g++ default-jdk-headless \

       make git pkg-config libcap-dev \

    && git clone --depth=1 https://github.com/ioi/isolate.git /tmp/isolate \

    && make -C /tmp/isolate isolate \

    && cp /tmp/isolate/isolate /usr/local/bin/isolate \

    && chmod +x /usr/local/bin/isolate \

    && rm -rf /tmp/isolate /var/lib/apt/lists/*

WORKDIR /app

COPY app ./app

CMD ["python", "-m", "app.main"]