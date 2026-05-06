#!/bin/bash

# setup.sh - Collab Installation Script
# Provides detailed feedback and error handling for Unix/Linux/macOS environments

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ERROR_COUNT=0

# Color codes for output
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
MAGENTA='\033[0;35m'
WHITE='\033[0;37m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "\n${CYAN}========================================"
    echo -e "   Collab Runtime Installation Script"
    echo -e "========================================${NC}\n"
}

print_step() {
    echo -e "${YELLOW}[Step $1/$2] $3${NC}"
}

print_error() {
    echo -e "${RED}Error: $1${NC}" >&2
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_info() {
    echo -e "${WHITE}$1${NC}"
}

print_banner

print_step 1 4 "Checking prerequisites..."

# Check Python
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 not found. Please install Python 3.10+ from https://www.python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "   Found: Python $PYTHON_VERSION" >&2

MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]); then
    print_error "Python 3.10+ required. Found Python $PYTHON_VERSION"
    exit 1
fi

print_success "Python $PYTHON_VERSION OK"

# Check Git
if ! command -v git &> /dev/null; then
    print_error "Git not found. Please install Git from https://git-scm.com"
    exit 1
fi

GIT_VERSION=$(git --version)
echo "   Found: $GIT_VERSION" >&2
print_success "Git OK"

print_step 2 4 "Setting up virtual environment..."

if [ ! -d ".venv" ]; then
    echo "   Creating ${MAGENTA}.venv${NC}..." >&2
    python3 -m venv .venv
    if [ $? -eq 0 ]; then
        print_success ".venv created"
    else
        print_error "Failed to create virtual environment"
        exit 1
    fi
else
    echo "   Virtual environment already exists" >&2
    print_success ".venv exists"
fi

print_step 3 4 "Installing dependencies..."

VENV_PYTHON=".venv/bin/python"
VENV_PIP=".venv/bin/pip"

if [ ! -f "$VENV_PIP" ]; then
    print_error "pip not found at $VENV_PIP"
    exit 1
fi

echo "   Ensuring pip is up to date..." >&2
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

if [ -f "requirements.txt" ]; then
    echo "   Installing core dependencies from ${MAGENTA}requirements.txt${NC}..." >&2

    if "$VENV_PIP" install -r requirements.txt; then
        print_success "Core dependencies installed"
    else
        print_error "Core dependencies installation failed"
        exit 1
    fi
else
    echo "   ${YELLOW}Warning: requirements.txt not found. Skipping core dependencies.${NC}" >&2
    ((ERROR_COUNT++))
fi

echo "   Installing collab package..." >&2
if "$VENV_PIP" install .; then
    print_success "collab package installed"
else
    print_error "collab package installation failed"
    ((ERROR_COUNT++))
fi

print_step 4 4 "Validating locking setup..."

echo "   Checking supabase-py import..." >&2
if "$VENV_PYTHON" -c "import supabase" 2>/dev/null; then
    print_success "supabase-py import OK"
else
    echo "   Installing supabase and python-dotenv..." >&2
    if "$VENV_PIP" install supabase python-dotenv --quiet; then
        if "$VENV_PYTHON" -c "import supabase" 2>/dev/null; then
            print_success "supabase-py installed"
        else
            print_error "supabase-py installation verification failed"
            ((ERROR_COUNT++))
        fi
    else
        print_error "Failed to install supabase/python-dotenv"
        ((ERROR_COUNT++))
    fi
fi

ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    HAS_URL=0
    HAS_ANON=0

    SUPABASE_URL_VALUE=$(grep -E '^SUPABASE_URL=' "$ENV_FILE" | head -n 1 | cut -d '=' -f 2-)
    SUPABASE_ANON_VALUE=$(grep -E '^SUPABASE_ANON_KEY=' "$ENV_FILE" | head -n 1 | cut -d '=' -f 2-)

    if [ -n "$SUPABASE_URL_VALUE" ] && [ "$SUPABASE_URL_VALUE" != "your_url_here" ]; then
        HAS_URL=1
    fi
    if [ -n "$SUPABASE_ANON_VALUE" ] && [ "$SUPABASE_ANON_VALUE" != "your_anon_key_here" ]; then
        HAS_ANON=1
    fi

    if [ $HAS_URL -eq 1 ] && [ $HAS_ANON -eq 1 ]; then
        print_success "Supabase credentials in .env"
    else
        echo "   ${YELLOW}Warning: .env exists but Supabase values look missing or placeholders.${NC}" >&2
        echo "   Set SUPABASE_URL and SUPABASE_ANON_KEY to real values." >&2
        ((ERROR_COUNT++))
    fi
else
    echo "   ${YELLOW}Warning: .env not found. Copy .env.example to .env and set Supabase credentials.${NC}" >&2
    ((ERROR_COUNT++))
fi

if command -v pre-commit >/dev/null 2>&1; then
    echo "   Installing git hooks via pre-commit..." >&2
    HOOK_INSTALL_FAILED=0
    for hook_type in pre-commit pre-push commit-msg; do
        if ! pre-commit install --hook-type "$hook_type" --overwrite >/dev/null 2>&1; then
            HOOK_INSTALL_FAILED=1
            break
        fi
    done

    if [ $HOOK_INSTALL_FAILED -eq 0 ]; then
        print_success "Git hooks installed"
        if [ -f "$PROJECT_ROOT/install_hooks.sh" ]; then
            if sh "$PROJECT_ROOT/install_hooks.sh" >/dev/null 2>&1; then
                print_success "Collab hook overlay installed"
            else
                echo "   ${YELLOW}Warning: collab hook overlay installation failed.${NC}" >&2
                ((ERROR_COUNT++))
            fi
        else
            echo "   ${YELLOW}Warning: install_hooks.sh not found.${NC}" >&2
            ((ERROR_COUNT++))
        fi
    else
        echo "   ${YELLOW}Warning: pre-commit hook installation failed.${NC}" >&2
        ((ERROR_COUNT++))
    fi
else
    echo "   ${YELLOW}Warning: pre-commit not found. Install it manually for repository hooks.${NC}" >&2
fi

echo ""
echo -e "${CYAN}========================================"
if [ $ERROR_COUNT -eq 0 ]; then
    echo -e "   Installation Complete!"
    echo -e "${GREEN}✓ Setup successful${NC}"
else
    echo -e "   Installation completed with ${YELLOW}$ERROR_COUNT warning(s)${NC}"
fi
echo -e "========================================${NC}\n"

if [ $ERROR_COUNT -ne 0 ]; then
    exit 1
fi
