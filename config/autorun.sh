#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Создан config.json из шаблона. Проверьте настройки и запустите скрипт повторно."
  exit 0
fi
python3 -m pip install -r requirements.txt
python3 libs/main.py --config config.json "$@"
