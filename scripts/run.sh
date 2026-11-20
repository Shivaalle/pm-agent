#!/usr/bin/env bash
# ==============================================================================
# AI Project Manager Agent - one-command bootstrap
# ==============================================================================
# Default: build the React app and serve everything from FastAPI on :8000/app/
# Dev:     ./scripts/run.sh --dev  (FastAPI :8000 + Vite HMR on :5173)
# ==============================================================================

set -o pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"
mkdir -p "${ROOT_DIR}/logs"

DEV_MODE=0
if [ "${1:-}" = "--dev" ]; then
  DEV_MODE=1
fi

echo -e "${BOLD}${BLUE}=====================================================${RESET}"
echo -e "${BOLD}${BLUE}     AI Project Manager Agent - Bootstrapper         ${RESET}"
echo -e "${BOLD}${BLUE}=====================================================${RESET}"

# 1. Python ---------------------------------------------------------------
echo -e "\n${BOLD}[1/5] Checking Python environment...${RESET}"
if ! command -v python3 &>/dev/null; then
  echo -e "  ${RED}✘ Python 3 is not installed or not in PATH.${RESET}"
  exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo -e "  ${GREEN}✔${RESET} Python ${BOLD}${PY_VER}${RESET}"

# 2. Virtual environment --------------------------------------------------
echo -e "\n${BOLD}[2/5] Checking virtual environment (.venv)...${RESET}"
if [ ! -x ".venv/bin/python" ]; then
  echo -e "  ${YELLOW}⚡ Creating .venv...${RESET}"
  python3 -m venv .venv
fi
VENV_PY="${ROOT_DIR}/.venv/bin/python"
VENV_PIP="${ROOT_DIR}/.venv/bin/pip"
echo -e "  ${GREEN}✔${RESET} .venv ready"

# 3. Dependencies ---------------------------------------------------------
echo -e "\n${BOLD}[3/5] Verifying dependencies...${RESET}"
MISSING_DEPS=$("${VENV_PY}" -c "
mods = ['fastapi', 'uvicorn', 'pydantic', 'google.genai', 'langgraph', 'mcp',
        'aiosqlite', 'httpx', 'yaml', 'dotenv', 'multipart']
print(','.join([m for m in mods if __import__('importlib').util.find_spec(m) is None]))
" 2>/dev/null || echo "check_failed")
if [ -n "${MISSING_DEPS}" ]; then
  echo -e "  ${YELLOW}⚡ Installing Python dependencies (${MISSING_DEPS})...${RESET}"
  "${VENV_PIP}" install --upgrade pip >/dev/null 2>&1 || true
  "${VENV_PIP}" install -r requirements.txt
fi
echo -e "  ${GREEN}✔${RESET} Python dependencies ready"

if ! command -v npm &>/dev/null; then
  echo -e "  ${RED}✘ Node.js/npm is required for the React frontend.${RESET}"
  exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
  echo -e "  ${YELLOW}⚡ Installing frontend dependencies...${RESET}"
  npm --prefix frontend install
fi

if [ "${DEV_MODE}" -eq 0 ]; then
  echo -e "  ${YELLOW}⚡ Building the React app...${RESET}"
  if ! npm --prefix frontend run build; then
    echo -e "  ${RED}✘ Frontend build failed.${RESET}"
    exit 1
  fi
  echo -e "  ${GREEN}✔${RESET} React build written to frontend/dist/"
else
  echo -e "  ${GREEN}✔${RESET} Dev mode: Vite will compile on demand"
fi

# 4. Configuration --------------------------------------------------------
echo -e "\n${BOLD}[4/5] Checking environment configuration...${RESET}"
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo -e "  ${YELLOW}⚠ Created .env from .env.example. Add live credentials or run offline.${RESET}"
else
  echo -e "  ${GREEN}✔${RESET} .env detected"
fi
if [ ! -f "data/mock_transcript.json" ]; then
  echo -e "  ${YELLOW}⚠ data/mock_transcript.json missing; sample loading will be unavailable.${RESET}"
fi

# 5. Ports ----------------------------------------------------------------
echo -e "\n${BOLD}[5/5] Checking ports...${RESET}"
cleanup_port() {
  local PORT=$1
  local PID
  PID=$(lsof -ti :"${PORT}" 2>/dev/null || true)
  if [ -n "${PID}" ]; then
    echo -e "  ${YELLOW}⚠ Port :${PORT} in use by PID ${PID}; stopping it.${RESET}"
    kill -9 ${PID} 2>/dev/null || true
    sleep 1
  fi
}
cleanup_port 8000
if [ "${DEV_MODE}" -eq 1 ]; then
  cleanup_port 5173
  echo -e "  ${GREEN}✔${RESET} Ports :8000 (API) and :5173 (Vite) are ready"
else
  echo -e "  ${GREEN}✔${RESET} Port :8000 (API + React) is ready"
fi

BACKEND_PID=""
FRONTEND_PID=""
shutdown_services() {
  echo -e "\n${BOLD}${YELLOW}Stopping services...${RESET}"
  [ -n "${FRONTEND_PID}" ] && kill "${FRONTEND_PID}" 2>/dev/null || true
  [ -n "${BACKEND_PID}" ] && kill "${BACKEND_PID}" 2>/dev/null || true
  cleanup_port 8000
  [ "${DEV_MODE}" -eq 1 ] && cleanup_port 5173
  echo -e "${GREEN}All services stopped.${RESET}"
  exit 0
}
trap shutdown_services SIGINT SIGTERM EXIT

export SCHEDULER_ENABLED="${SCHEDULER_ENABLED:-true}"

echo -e "\n${BOLD}${BLUE}Starting FastAPI on :8000...${RESET}"
"${VENV_PY}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/uvicorn.log 2>&1 &
BACKEND_PID=$!

echo -n "  Waiting for the API"
HEALTH_OK=0
for _ in {1..40}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 0.5
  echo -n "."
done
echo ""
if [ ${HEALTH_OK} -ne 1 ]; then
  echo -e "  ${RED}✘ Backend failed to start. See logs/uvicorn.log${RESET}"
  exit 1
fi
echo -e "  ${GREEN}✔${RESET} API healthy"

if [ "${DEV_MODE}" -eq 1 ]; then
  echo -e "\n${BOLD}${BLUE}Starting Vite dev server on :5173...${RESET}"
  VITE_API_PROXY="http://127.0.0.1:8000" npm --prefix frontend run dev > logs/vite.log 2>&1 &
  FRONTEND_PID=$!
  sleep 2
  URL="http://localhost:5173/app/"
else
  URL="http://localhost:8000/app/"
fi

echo -e "\n${BOLD}${GREEN}=====================================================${RESET}"
echo -e "${BOLD}${GREEN}  AI Project Manager Agent is running${RESET}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "  ${BOLD}Web app:${RESET}        ${BLUE}${URL}${RESET}"
echo -e "  ${BOLD}API docs:${RESET}       ${BLUE}http://localhost:8000/docs${RESET}"
if [ "${DEV_MODE}" -eq 0 ]; then
  echo -e "  ${BOLD}React dev mode:${RESET} ${BLUE}./scripts/run.sh --dev${RESET}"
fi
echo -e "  ${BOLD}Backend log:${RESET}    ${BLUE}logs/uvicorn.log${RESET}"
echo -e "  ${BOLD}Demo accounts:${RESET}  sarah / alex / david / elena / mark / admin"
echo -e "  ${BOLD}Demo password:${RESET}  ${DEMO_PASSWORD:-demo1234}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "Press ${BOLD}[Ctrl+C]${RESET} to stop."

wait
