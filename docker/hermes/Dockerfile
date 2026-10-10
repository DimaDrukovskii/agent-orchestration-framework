# Hermes Agent (Nous Research) в изолированном контейнере: «мозг» Алины.
# Модель — через Polza AI по нашему API-ключу (не подписка Nous).
# Установщик и релиз закреплены: скрипт проверяется по sha256 (его содержимое
# прочитано перед первой сборкой), код агента — коммит main, совпадающий с установщиком.
FROM python:3.12-slim-bookworm

ARG INSTALLER_SHA256=034845e34289813ff5fb7fd3e071ec69f6b14aee8ba297a477b31dda6374cb86
ARG HERMES_COMMIT=dce1e9b37581dd62e480a9064dc04a709c2940d3

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git ca-certificates xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# не root: у агента есть инструменты (терминал и др.), контейнер — граница изоляции
RUN useradd --create-home --shell /bin/bash hermes
USER hermes
WORKDIR /home/hermes
ENV HOME=/home/hermes \
    HERMES_HOME=/home/hermes/.hermes \
    PATH=/home/hermes/.local/bin:$PATH

RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh -o /tmp/install.sh \
    && echo "${INSTALLER_SHA256}  /tmp/install.sh" | sha256sum -c - \
    && bash /tmp/install.sh --non-interactive --skip-browser --skip-computer-use --commit "${HERMES_COMMIT}"

COPY --chown=hermes:hermes entrypoint.sh /home/hermes/entrypoint.sh
RUN chmod +x /home/hermes/entrypoint.sh

EXPOSE 8642
ENTRYPOINT ["/home/hermes/entrypoint.sh"]
