#!/bin/bash
# Оставлено для привычного пути: вся установка в scripts/setup.py (нужны только Python 3 и Docker).
exec python3 "$(dirname "$0")/setup.py" "$@"
