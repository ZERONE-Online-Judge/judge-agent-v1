FROM eclipse-temurin:21-jdk-noble@sha256:107215954359f1ab0be058a247d7c64ed6e69c71c1682d471d38ef751ec8e964 AS zoj-jdk
FROM python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8 AS cpython311
FROM python:3.13.16-slim-trixie@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       ca-certificates \
       gcc \
       g++ \
       binutils \
       libc6-dev \
       make \
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

COPY --from=zoj-jdk /opt/java/openjdk /usr/local/lib/zoj-jdk
COPY --from=cpython311 /usr/local /usr/local/lib/zoj-python311
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip bzip2 libffi8 libncursesw6 libtinfo6 && rm -rf /var/lib/apt/lists/*
COPY deploy/install-zoj-runtimes.sh /tmp/install-zoj-runtimes.sh
RUN sh /tmp/install-zoj-runtimes.sh && rm /tmp/install-zoj-runtimes.sh

WORKDIR /app

COPY app ./app

CMD ["python", "-m", "app.main"]