#!/usr/bin/env bash
# Собирает конфиг Hermes из переменных окружения и запускает шлюз с
# OpenAI-совместимым API-сервером (порт 8642). Ключи в образ не попадают.
set -euo pipefail

model_url="${HERMES_MODEL_BASE_URL:-https://polza.ai/api/v1}"
case "${model_url%/}" in
  https://polza.ai/api/v1)
    model_key="${HERMES_MODEL_API_KEY:-${POLZA_API_KEY:?POLZA_API_KEY не задан}}"
    ;;
  https://openrouter.ai/api/v1)
    model_key="${HERMES_MODEL_API_KEY:-${OPENROUTER_API_KEY:?OPENROUTER_API_KEY не задан}}"
    : "${HERMES_MODEL:?Для OpenRouter укажите HERMES_MODEL}"
    ;;
  *)
    model_key="${HERMES_MODEL_API_KEY:-local}"
    ;;
esac
: "${API_SERVER_KEY:?API_SERVER_KEY не задан}"

# Hermes включает API-сервер, только если ключ не короче 16 символов
# (иначе молча пишет «No messaging platforms enabled»).
if [ "${#API_SERVER_KEY}" -lt 16 ]; then
  echo "API_SERVER_KEY должен быть не короче 16 символов" >&2
  exit 1
fi

mkdir -p "$HERMES_HOME"

cat > "$HERMES_HOME/.env" <<ENV
HERMES_MODEL_API_KEY=${model_key}
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=${API_SERVER_KEY}
ENV

cat > "$HERMES_HOME/config.yaml" <<YAML
model:
  default: ${HERMES_MODEL:-anthropic/claude-sonnet-4}
  provider: custom
  base_url: "${model_url}"
  api_key: "${model_key}"
  context_length: ${HERMES_CONTEXT_LENGTH:-200000}
YAML

if [ -n "${HERMES_MCP_CONFIG:-}" ]; then
  printf '\n' >> "$HERMES_HOME/config.yaml"
  cat "$HERMES_MCP_CONFIG" >> "$HERMES_HOME/config.yaml"
fi

exec hermes gateway run
