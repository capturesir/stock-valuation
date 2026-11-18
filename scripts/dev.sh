#!/usr/bin/env bash
# Local development and testing in one command (Linux / macOS).
#
#   scripts/dev.sh                 start PostgreSQL + Redis (Docker), the API (:8000) and the web app (:5173)
#   scripts/dev.sh admin EMAIL     create an administrator (asks for the password)
#   scripts/dev.sh test            run every test: v1 engine, v2 API (PostgreSQL), frontend
#   scripts/dev.sh db              start only PostgreSQL + Redis
#   scripts/dev.sh stop            stop PostgreSQL + Redis (data is kept)
#
# Python: the interpreter in $SV_PYTHON, else ./.venv, else ~/.venvs/stock-valuation, else python3.
# The database runs in Docker with the defaults the backend expects (valuation / change-me on
# 127.0.0.1:5432); .env is not read here, so an old-format .env does not get in the way.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -n "${SV_PYTHON:-}" ]]; then PY="$SV_PYTHON"
elif [[ -x .venv/bin/python ]]; then PY=.venv/bin/python
elif [[ -x "$HOME/.venvs/stock-valuation/bin/python" ]]; then PY="$HOME/.venvs/stock-valuation/bin/python"
else PY=python3; fi
BIN="$(dirname "$PY")"

compose() {
  APP_SECRET_KEY="${APP_SECRET_KEY:-dev-only-not-secret}" \
    docker compose --env-file /dev/null -f docker-compose.yml -f docker-compose.dev.yml "$@"
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "需要 $1：$2" >&2; exit 1; }; }

db_up() {
  need docker "請先安裝 Docker（或自行安裝 PostgreSQL 16 並設定 DATABASE_URL）"
  compose up -d db redis >/dev/null 2>&1 || compose up -d db redis
  printf "等待 PostgreSQL 就緒"
  for _ in $(seq 1 60); do
    if compose exec -T db pg_isready -U valuation >/dev/null 2>&1; then echo " ✓"; return 0; fi
    printf "."; sleep 1
  done
  echo; echo "PostgreSQL 沒有在 60 秒內就緒：docker compose logs db" >&2; exit 1
}

deps() {
  "$PY" -c "import fastapi, sqlalchemy, argon2, multipart, PIL" 2>/dev/null || {
    echo "安裝後端套件…"; "$PY" -m pip install -q -r backend/requirements.txt; }
  "$PY" -m ruff --version >/dev/null 2>&1 || "$PY" -m pip install -q ruff
  need pnpm "請執行 corepack enable（Node.js 20 以上）"
  [[ -d frontend/node_modules ]] || (cd frontend && pnpm install --frozen-lockfile)
}

port_free() {
  if (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; then
    echo "埠 $1 已被使用（可能已有一個 API 或網頁在運行）。請先關閉它。" >&2; exit 1
  fi
}

case "${1:-up}" in
  up)
    deps; db_up; port_free 8000; port_free 5173
    "$BIN/uvicorn" backend.app.main:app --port 8000 --reload --reload-dir backend --reload-dir src &
    # on Ctrl+C or any exit, stop everything this script started (API, Vite) — not just the script
    trap 'trap - INT TERM EXIT; kill 0 2>/dev/null' INT TERM EXIT
    for _ in $(seq 1 30); do curl -sf localhost:8000/api/v1/health >/dev/null && break; sleep 1; done
    if [[ "$(curl -sf localhost:8000/api/v1/auth/status)" == *'"needs_setup":true'* ]]; then
      echo; echo "⚠ 尚未有帳號。另開一個終端機執行：scripts/dev.sh admin 你的電郵"; echo
    fi
    echo "網頁：http://localhost:5173    API 文件：http://localhost:8000/api/v1/docs    （Ctrl+C 結束）"
    # in the background + wait, so a TERM (not only Ctrl+C) runs the trap at once
    (cd frontend && exec pnpm dev) &
    wait
    ;;
  admin)
    [[ -n "${2:-}" ]] || { echo "用法：scripts/dev.sh admin 你的電郵 [名稱]" >&2; exit 1; }
    db_up
    "$PY" -m backend.app.cli create-user --email "$2" --name "${3:-}" --role admin
    ;;
  test)
    deps; db_up
    echo "== lint";       "$BIN/ruff" check . 2>/dev/null || "$PY" -m ruff check .
    echo "== v1 引擎";    "$PY" -m pytest -q
    echo "== v2 API";     "$PY" -m pytest backend/tests -q
    echo "== 前端";       (cd frontend && pnpm build:tokens >/dev/null && pnpm typecheck && pnpm test && pnpm build >/dev/null && echo "前端建置 ✓")
    echo "== 版本號";     "$PY" scripts/bump_version.py --check
    ;;
  db)   db_up ;;
  stop) compose stop db redis ;;
  *)    sed -n '2,12p' "$0"; exit 1 ;;
esac
