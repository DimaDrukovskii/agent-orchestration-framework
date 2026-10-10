#!/usr/bin/env bash
# Собирает конфиг Hermes из переменных окружения и запускает шлюз с
# OpenAI-совместимым API-сервером (порт 8642). Ключи в образ не попадают.
set -euo pipefail

: "${POLZA_API_KEY:?POLZA_API_KEY не задан}"
: "${API_SERVER_KEY:?API_SERVER_KEY не задан}"

# Hermes включает API-сервер, только если ключ не короче 16 символов
# (иначе молча пишет «No messaging platforms enabled»).
if [ "${#API_SERVER_KEY}" -lt 16 ]; then
  echo "API_SERVER_KEY должен быть не короче 16 символов" >&2
  exit 1
fi

mkdir -p "$HERMES_HOME"

cat > "$HERMES_HOME/.env" <<ENV
POLZA_API_KEY=${POLZA_API_KEY}
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=${API_SERVER_KEY}
ENV

cat > "$HERMES_HOME/config.yaml" <<YAML
model:
  default: ${HERMES_MODEL:-anthropic/claude-sonnet-4}
  provider: custom
  base_url: https://polza.ai/api/v1
  api_key: "${POLZA_API_KEY}"
  context_length: ${HERMES_CONTEXT_LENGTH:-200000}
YAML

exec hermes gateway run
