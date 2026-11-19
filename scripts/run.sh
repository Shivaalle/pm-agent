#!/usr/bin/env bash

# ==============================================================================
# AI Project Manager Agent - Universal Startup & Setup Script
# ==============================================================================
# Checks prerequisites, initializes virtual environment, installs dependencies,
# verifies configurations, and boots both FastAPI backend and Streamlit frontend.
# ==============================================================================

set -o pipefail

# Text formatting
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

# Resolve absolute root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"
mkdir -p "${ROOT_DIR}/logs"

echo -e "${BOLD}${BLUE}=====================================================${RESET}"
echo -e "${BOLD}${BLUE}     AI Project Manager Agent - Service Bootstrapper  ${RESET}"
echo -e "${BOLD}${BLUE}=====================================================${RESET}"

# 1. Check Python installation
echo -e "\n${BOLD}[1/5] Checking Python environment...${RESET}"
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    echo -e "  ${GREEN}✔${RESET} Python 3 detected: ${BOLD}${PY_VER}${RESET}"
else
    echo -e "  ${RED}✘ Error:${RESET} Python 3 is not installed or not in PATH."
    exit 1
fi

# 2. Check Virtual Environment (.venv)
echo -e "\n${BOLD}[2/5] Checking virtual environment (.venv)...${RESET}"
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/python" ]; then
    echo -e "  ${YELLOW}⚡ Virtual environment not found. Creating .venv...${RESET}"
    python3 -m venv .venv
    echo -e "  ${GREEN}✔${RESET} Created .venv successfully."
else
    echo -e "  ${GREEN}✔${RESET} Virtual environment .venv already exists."
fi

VENV_PY="${ROOT_DIR}/.venv/bin/python"
VENV_PIP="${ROOT_DIR}/.venv/bin/pip"

# 3. Check and install dependencies
echo -e "\n${BOLD}[3/5] Verifying dependencies...${RESET}"
MISSING_DEPS=$("${VENV_PY}" -c "
mods = ['fastapi', 'uvicorn', 'streamlit', 'pydantic', 'google.genai', 'langgraph', 'mcp', 'aiosqlite', 'httpx', 'yaml']
missing = []
for m in mods:
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
print(','.join(missing))
" 2>/dev/null || echo "check_failed")

if [ -n "${MISSING_DEPS}" ] && [ "${MISSING_DEPS}" != "" ]; then
    echo -e "  ${YELLOW}⚡ Installing required dependencies from requirements.txt...${RESET}"
    "${VENV_PIP}" install --upgrade pip >/dev/null 2>&1 || true
    "${VENV_PIP}" install -r requirements.txt
    echo -e "  ${GREEN}✔${RESET} Dependencies successfully installed."
else
    echo -e "  ${GREEN}✔${RESET} All core dependencies verified."
fi

# 4. Check Environment Configuration (.env)
echo -e "\n${BOLD}[4/5] Checking environment configuration (.env)...${RESET}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo -e "  ${YELLOW}⚠ .env file not found. Generating from .env.example...${RESET}"
        cp .env.example .env
        echo -e "  ${YELLOW}⚠ Please edit .env with your GEMINI_API_KEY and Jira/GitHub tokens.${RESET}"
    else
        echo -e "  ${RED}✘ Warning:${RESET} .env file not found."
    fi
else
    echo -e "  ${GREEN}✔${RESET} .env configuration file detected."
fi

# 5. Check and release lingering ports (8000 and 8501)
echo -e "\n${BOLD}[5/5] Checking port availability...${RESET}"
cleanup_port() {
    local PORT=$1
    local PID=$(lsof -ti :${PORT} 2>/dev/null || true)
    if [ -n "${PID}" ]; then
        echo -e "  ${YELLOW}⚠ Port :${PORT} is in use by PID ${PID}. Terminating old process...${RESET}"
        kill -9 ${PID} 2>/dev/null || true
        sleep 1
    fi
}
cleanup_port 8000
cleanup_port 8501
echo -e "  ${GREEN}✔${RESET} Ports :8000 (Backend API) and :8501 (Frontend UI) are ready."

# Process cleanup handler on exit/interrupt
BACKEND_PID=""
FRONTEND_PID=""

shutdown_services() {
    echo -e "\n\n${BOLD}${YELLOW}Stopping all services gracefully...${RESET}"
    if [ -n "${BACKEND_PID}" ]; then
        kill "${BACKEND_PID}" 2>/dev/null || true
    fi
    if [ -n "${FRONTEND_PID}" ]; then
        kill "${FRONTEND_PID}" 2>/dev/null || true
    fi
    cleanup_port 8000
    cleanup_port 8501
    echo -e "${GREEN}All services stopped cleanly. Goodbye!${RESET}"
    exit 0
}

trap shutdown_services SIGINT SIGTERM EXIT

# Start Backend
echo -e "\n${BOLD}${BLUE}Starting FastAPI Backend on :8000...${RESET}"
"${VENV_PY}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/uvicorn.log 2>&1 &
BACKEND_PID=$!

# Wait for backend healthcheck
echo -n "  Waiting for backend API to initialize..."
HEALTH_OK=0
for i in {1..20}; do
    if curl -s http://127.0.0.1:8000/ >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 0.5
    echo -n "."
done
echo ""

if [ ${HEALTH_OK} -eq 1 ]; then
    echo -e "  ${GREEN}✔${RESET} Backend is healthy and ready!"
else
    echo -e "  ${RED}✘ Backend failed to start within timeout. Check logs/app.log and logs/uvicorn.log${RESET}"
    exit 1
fi

# Start Frontend
echo -e "\n${BOLD}${BLUE}Starting Streamlit Frontend on :8501...${RESET}"
"${VENV_PY}" -m streamlit run frontend/app.py \
    --server.port 8501 \
    --server.headless true \
    --browser.gatherUsageStats false > logs/streamlit.log 2>&1 &
FRONTEND_PID=$!

sleep 2

echo -e "\n${BOLD}${GREEN}=====================================================${RESET}"
echo -e "${BOLD}${GREEN}  🚀 AI Project Manager Agent is UP AND RUNNING!     ${RESET}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "  ${BOLD}• Web Application (UI):${RESET}  ${BLUE}http://localhost:8501${RESET}"
echo -e "  ${BOLD}• API Documentation:${RESET}    ${BLUE}http://localhost:8000/docs${RESET}"
echo -e "  ${BOLD}• Application Logs:${RESET}     ${BLUE}logs/app.log${RESET}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "Press ${BOLD}[Ctrl+C]${RESET} anytime to shut down both services."

# Keep parent script running to monitor child processes
wait
