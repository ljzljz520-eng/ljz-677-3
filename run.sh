#!/usr/bin/env bash
# 启动体检结果批量上送应用
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv --without-pip .venv || python3 -m venv .venv
  .venv/bin/python -m pip --version >/dev/null 2>&1 || \
    curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
