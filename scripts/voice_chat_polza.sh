#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo 'Создайте .env с ключом ElevenLabs.' >&2
  exit 1
fi
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo 'Установите зависимости: uv venv --python python3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt' >&2
  exit 1
fi
if ! grep -Eq '^POLZA_API_KEY=.+$' .env; then
  echo 'Добавьте POLZA_API_KEY в .env. Ключ не передавайте в чат.' >&2
  exit 1
fi

export LLM_PROVIDER=polza
export TELEPHONY_ENABLED=false
export CALLBACK_SIMULATOR_ENABLED=true
export AGENT_API_KEY=test-local-key

echo 'Голосовой чат: http://127.0.0.1:8000/demo/call.html'
exec .venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8000
