FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl ca-certificates gcc g++ default-jdk-headless \
    && mkdir -p /etc/apt/keyrings /etc/apt/sources.list.d \
    && curl -fsSL https://www.ucw.cz/isolate/debian/signing-key.asc \
       -o /etc/apt/keyrings/isolate.asc \
    && printf '%s\n' \
       'Types: deb' \
       'URIs: http://www.ucw.cz/isolate/debian/' \
       'Suites: trixie-isolate' \
       'Components: main' \
       'Architectures: amd64' \
       'Signed-By: /etc/apt/keyrings/isolate.asc' \
       > /etc/apt/sources.list.d/isolate.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends isolate \
    && apt-get install -y --no-install-recommends \
       gcc g++ python3 default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app ./app
CMD ["python", "-m", "app.main"]