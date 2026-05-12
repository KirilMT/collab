#!/bin/bash

# setup-dev.sh - Development Environment Setup for Linux/macOS
# Calls setup.sh for production setup, then adds dev-specific tools

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Color codes
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
MAGENTA='\033[0;35m'
WHITE='\033[0;37m'
GRAY='\033[0;90m'
NC='\033[0m'

echo -e "${CYAN}========================================"
echo -e "   Collab Development Setup"
echo -e "========================================${NC}\n"

ERROR_COUNT=0

# ============================================================================
# STEP 1: RUN PRODUCTION SETUP
# ============================================================================
echo -e "${MAGENTA}========================================"
echo -e "   PRODUCTION SETUP"
echo -e "========================================${NC}\n"

if [ ! -f "scripts/setup.sh" ]; then
    echo -e "${RED}Error: scripts/setup.sh not found.${NC}"
    exit 1
fi

echo -e "${YELLOW}Running production setup (setup.sh)...${NC}\n"
./scripts/setup.sh --called-from-dev

echo -e "\n${GREEN}========================================"
echo -e "   Production Setup Complete"
echo -e "========================================${NC}\n"

# ============================================================================
# STEP 2: DEVELOPMENT TOOLS SETUP
# ============================================================================
echo -e "${MAGENTA}========================================"
echo -e "   DEVELOPMENT TOOLS SETUP"
echo -e "========================================${NC}\n"

# Step 1: Check for Node.js
echo -e "${YELLOW}[Dev Step 1/6] Checking Node.js...${NC}"
if command -v npm >/dev/null 2>&1; then
    NPM_VERSION=$(npm --version)
    echo -e "   Found: ${WHITE}npm $NPM_VERSION${NC} ${GREEN}OK${NC}"
else
    echo -e "   ${RED}Node.js/npm not found. Please install Node.js (LTS recommended) from https://nodejs.org${NC}"
    exit 1
fi

# Step 2: Check for GitHub CLI
echo -e "\n${YELLOW}[Dev Step 2/6] Checking GitHub CLI...${NC}"
if command -v gh >/dev/null 2>&1; then
    GH_VERSION=$(gh --version | head -n 1 | awk '{print $3}')
    echo -e "   Found: ${WHITE}gh $GH_VERSION${NC} ${GREEN}OK${NC}"
else
    echo -e "   ${GRAY}GitHub CLI not found (optional). Install from https://cli.github.com${NC}"
fi

# Step 3: Python Development Tools
echo -e "\n${YELLOW}[Dev Step 3/6] Installing Python development tools...${NC}"
if [ -d ".venv/bin" ]; then
    PIP_CMD=".venv/bin/pip"
else
    PIP_CMD=".venv/Scripts/pip"
fi

if [ -f "requirements-dev.txt" ]; then
    echo -e "   Ensuring all dev dependencies are installed and up-to-date..."
    if "$PIP_CMD" install --upgrade --upgrade-strategy only-if-needed -r requirements-dev.txt >/dev/null 2>&1; then
        echo -e "   ${WHITE}Python dev dependencies are present and up-to-date${NC} ${GREEN}OK${NC}"
    else
        echo -e "   ${RED}Python dev dependencies installation FAILED${NC}"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
else
    echo -e "   ${YELLOW}Warning: requirements-dev.txt not found.${NC}"
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# Step 4: JavaScript Development Tools
echo -e "\n${YELLOW}[Dev Step 4/6] Setting up JavaScript development tools...${NC}"
if [ ! -f "package.json" ]; then
    echo -en "   Initializing ${MAGENTA}package.json${NC}... "
    npm init -y >/dev/null 2>&1
    echo -e "${GREEN}OK${NC}"
else
    echo -e "   package.json already exists ${GREEN}OK${NC}"
fi

echo -en "   Installing ${MAGENTA}prettier + prettier-plugin-yaml${NC}... "
if npm install --save-dev prettier prettier-plugin-yaml >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}FAILED${NC}"
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# Step 5: Git Template + Pre-commit Hooks
echo -e "\n${YELLOW}[Dev Step 5/6] Setting up Conventional Commit template and hooks...${NC}"
if [ -f ".gitmessage" ]; then
    git config --local commit.template .gitmessage
    echo -e "   ${GREEN}[OK] .gitmessage set as commit template${NC}"
else
    echo -e "   ${YELLOW}[WARN] .gitmessage not found, skipping commit template setup${NC}"
fi

if [ -d ".venv/bin" ]; then
    PRECOMMIT_CMD=".venv/bin/pre-commit"
else
    PRECOMMIT_CMD=".venv/Scripts/pre-commit"
fi

if [ -f "$PRECOMMIT_CMD" ]; then
    VERSION=$("$PRECOMMIT_CMD" --version)
    echo -e "   Using: ${WHITE}$VERSION${NC} ${GREEN}OK${NC}"
    echo -e "   Installing repository hooks (framework mode)..."
    for hook in pre-commit pre-push commit-msg; do
        echo -en "     - Installing $hook hook... "
        "$PRECOMMIT_CMD" install --hook-type "$hook" --overwrite >/dev/null 2>&1
        echo -e "${GREEN}OK${NC}"
    done
else
    echo -e "   ${YELLOW}Pre-commit not found. Skipping hook installation.${NC}"
fi

# Step 6: Supabase Setup
echo -e "\n${YELLOW}[Dev Step 6/6] Configure Supabase locking settings...${NC}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "   ${GREEN}Created .env from .env.example${NC}"
    fi
fi

if [ -f ".env" ]; then
    echo -e "   Supabase configuration is required for live collaborative locks."
    if grep -q "SUPABASE_URL=" .env && grep -q "SUPABASE_ANON_KEY=" .env; then
        echo -e "   ${WHITE}Supabase key entries present in .env${NC} ${GREEN}OK${NC}"
    else
        echo -e "   ${YELLOW}Missing required Supabase entries in .env${NC}"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
fi


echo -e "\n${CYAN}========================================"
if [ $ERROR_COUNT -eq 0 ]; then
    echo -e "   Development Setup Complete!"
    echo -e "   ${GRAY}(Production + Dev Tools + Daemon Active)${NC}"
else
    echo -e "   Setup completed with ${YELLOW}$ERROR_COUNT warning(s)${NC}"
fi
echo -e "========================================${NC}\n"

echo -e "\n${CYAN}================================================================"
echo -e "                        NEXT STEPS                              "
echo -e "================================================================${NC}"
echo ""
echo -e "${WHITE}  1. Collaborative daemon should be active (Core Step 9).
     Use 'collab active' to verify.
"
echo ""
echo -e "${WHITE}  2. Activate the virtual environment (if not already active):${NC}"
if [ -d ".venv/bin" ]; then
    echo -e "     source .venv/bin/activate"
else
    echo -e "     source .venv/Scripts/activate"
fi
echo ""
echo -e "${WHITE}  3. Run quality checks:${NC}"
echo -e "     python scripts/format_code.py"
echo -e "     python scripts/validate_code.py --quick"
echo ""
echo -e "${CYAN}================================================================${NC}\n"
