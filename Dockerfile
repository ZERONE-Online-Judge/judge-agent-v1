FROM eclipse-temurin:21.0.4_7-jdk-jammy AS icpc-jdk
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       ca-certificates \
       gcc \
       g++ \
       binutils \
       libc6-dev \
       make \
       default-jdk \
       docker-cli \
    \
    && mkdir -p /etc/apt/keyrings /etc/apt/sources.list.d \
    \
    && curl -fsSL https://www.ucw.cz/isolate/debian/signing-key.asc \
       -o /etc/apt/keyrings/isolate.asc \
    \
    && printf '%s\n' \
       'Types: deb' \
       'URIs: http://www.ucw.cz/isolate/debian/' \
       'Suites: trixie-isolate' \
       'Components: main' \
       'Architectures: amd64' \
       'Signed-By: /etc/apt/keyrings/isolate.asc' \
       > /etc/apt/sources.list.d/isolate.sources \
    \
    && apt-get update \
    && apt-get install -y --no-install-recommends isolate \
    \
    && docker --version \
    && rm -rf /var/lib/apt/lists/*

COPY --from=icpc-jdk /opt/java/openjdk /usr/local/lib/icpc-jdk
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip bzip2 libffi8 libncursesw6 libtinfo6 && rm -rf /var/lib/apt/lists/*
COPY deploy/install-icpc-runtimes.sh /tmp/install-icpc-runtimes.sh
RUN sh /tmp/install-icpc-runtimes.sh && rm /tmp/install-icpc-runtimes.sh

WORKDIR /app

COPY app ./app

CMD ["python", "-m", "app.main"]