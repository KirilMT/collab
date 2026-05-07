#!/bin/bash

# setup.sh - Enhanced Collab Installation Script
# Provides detailed feedback and error handling for Unix/Linux/macOS environments
# Supports non-interactive mode for automation and CI provisioning

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
    echo -e "   Collab Installation Script"
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

# Parse command line arguments
NON_INTERACTIVE=false
CALLED_FROM_DEV=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --non-interactive|-n)
            NON_INTERACTIVE=true
            shift
            ;;
        --called-from-dev)
            CALLED_FROM_DEV=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Only show header if not called from dev script
if [ "$CALLED_FROM_DEV" = false ]; then
    print_banner
fi

print_step 1 7 "Checking prerequisites..."

# Check Python (prefer python3, fallback to python if version output is valid)
PYTHON_CMD=""
PYTHON_VERSION=""

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        VERSION_OUTPUT=$($candidate --version 2>&1 || true)
        VERSION_VALUE=$(echo "$VERSION_OUTPUT" | awk '{print $2}')
        if echo "$VERSION_VALUE" | grep -Eq '^[0-9]+\.[0-9]+'; then
            PYTHON_CMD="$candidate"
            PYTHON_VERSION="$VERSION_VALUE"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    print_error "Python 3 not found. Please install Python 3.10+ from https://www.python.org"
    exit 1
fi

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

print_step 2 7 "Setting up virtual environment..."

if [ ! -d ".venv" ]; then
    echo "   Creating ${MAGENTA}.venv${NC}..." >&2
    "$PYTHON_CMD" -m venv .venv
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

print_step 3 7 "Installing core dependencies..."

VENV_LAYOUT=""
if [ -f ".venv/bin/python" ] && [ -f ".venv/bin/pip" ]; then
    VENV_PYTHON=".venv/bin/python"
    VENV_PIP=".venv/bin/pip"
    VENV_LAYOUT="posix"
elif [ -f ".venv/Scripts/python.exe" ] && [ -f ".venv/Scripts/pip.exe" ]; then
    VENV_PYTHON=".venv/Scripts/python.exe"
    VENV_PIP=".venv/Scripts/pip.exe"
    VENV_LAYOUT="windows"
else
    print_error "Could not locate virtual environment Python/pip under .venv/bin or .venv/Scripts"
    exit 1
fi

if [ ! -f "$VENV_PIP" ]; then
    print_error "pip not found at $VENV_PIP"
    exit 1
fi

echo "   Ensuring pip is up to date..." >&2
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

if [ -f "requirements.txt" ]; then
    echo "   Installing core dependencies from ${MAGENTA}requirements.txt${NC}..." >&2
    echo ""
    if "$VENV_PIP" install -r requirements.txt; then
        echo ""
        print_success "Core dependencies installed"
    else
        echo ""
        print_error "Core dependencies installation failed"
        exit 1
    fi
else
    echo "   ${YELLOW}Warning: requirements.txt not found. Skipping core dependencies.${NC}" >&2
    ((ERROR_COUNT++))
fi

print_step 4 7 "Installing collab package..."
echo "   Installing ${MAGENTA}collab${NC} from PyPI..." >&2

COLLAB_CHECK=$("$VENV_PIP" show collab 2>&1 || true)
if echo "$COLLAB_CHECK" | grep -q "^Name: collab"; then
    INSTALLED_VERSION=$(echo "$COLLAB_CHECK" | grep "^Version:" | cut -d' ' -f2)
    echo "   collab $INSTALLED_VERSION already installed" >&2
    print_success "collab installed"
else
    if [ -n "$COLLAB_VERSION" ]; then
        PACKAGE_SPEC="collab==$COLLAB_VERSION"
        echo "   Installing pinned version: $COLLAB_VERSION..." >&2
    else
        PACKAGE_SPEC="collab"
        echo "   Installing latest version from PyPI..." >&2
    fi

    if "$VENV_PIP" install "$PACKAGE_SPEC" --quiet; then
        INSTALLED_VERSION=$("$VENV_PIP" show collab 2>&1 | grep "^Version:" | cut -d' ' -f2)
        echo "   collab $INSTALLED_VERSION installed" >&2
        print_success "collab installed"
    else
        print_error "collab package installation failed"
        ((ERROR_COUNT++))
    fi
fi

print_step 5 7 "Configuring environment..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp ".env.example" ".env"
        echo "   Created ${MAGENTA}.env${NC} from ${MAGENTA}.env.example${NC}" >&2
        print_success ".env created"
    else
        echo "   ${YELLOW}Warning: .env.example not found. You will need to create .env manually.${NC}" >&2
        ((ERROR_COUNT++))
    fi
else
    echo "   ${MAGENTA}.env${NC} already exists" >&2
    print_success ".env exists"
fi

echo ""
echo -e "${YELLOW}[Locking Setup] Validating collaborative locking prerequisites...${NC}"

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
    echo "   ${YELLOW}Warning: pre-commit not found. Run ./scripts/setup-dev.sh to install repository hooks.${NC}" >&2
fi

print_step 6 7 "Installing VS Code extension (optional)..."

VSCODE_EXT_DIR="$PROJECT_ROOT/vscode-extension/collab-locks"
if [ -f "$VSCODE_EXT_DIR/package.json" ]; then
    echo "   Found VS Code extension at ${MAGENTA}vscode-extension/collab-locks${NC} ..." >&2

    if command -v code >/dev/null 2>&1; then
        echo "   VS Code CLI found, installing extension..." >&2
        if (cd "$VSCODE_EXT_DIR" && code --install-extension . --force >/dev/null 2>&1); then
            print_success "VS Code extension installed"
        else
            echo "   ${YELLOW}Warning: VS Code extension installation failed (non-critical).${NC}" >&2
        fi
    else
        echo "   ${YELLOW}VS Code CLI not found. Extension must be installed manually:${NC}" >&2
        echo "     1. Open VS Code" >&2
        echo "     2. Go to Extensions (Ctrl+Shift+X)" >&2
        echo "     3. Search for 'collab-locks'" >&2
        echo "     Or run: code --install-extension vscode-extension/collab-locks" >&2
    fi
else
    echo "   VS Code extension not found" >&2
    echo "   ${YELLOW}SKIPPED${NC}" >&2
fi

print_step 7 7 "Running smoke tests..."

SMOKE_TESTS_PASSED=true

echo "   Testing collab command availability..." >&2
if [ "$VENV_LAYOUT" = "windows" ]; then
    COLLAB_CMD=".venv/Scripts/collab.exe"
else
    COLLAB_CMD=".venv/bin/collab"
fi
if [ -f "$COLLAB_CMD" ]; then
    # Use --help for health check because collab CLI does not expose --version.
    if "$COLLAB_CMD" --help >/dev/null 2>&1; then
        print_success "collab command available"
    else
        echo "   ${YELLOW}Warning: collab command available but failed health check${NC}" >&2
        SMOKE_TESTS_PASSED=false
    fi
else
    echo "   ${YELLOW}Warning: collab command not found${NC}" >&2
    SMOKE_TESTS_PASSED=false
fi

echo "   Validating Supabase configuration..." >&2
if [ -f ".env" ]; then
    if grep -q "SUPABASE_URL.*=" ".env" && grep -q "SUPABASE_ANON_KEY.*=" ".env"; then
        print_success "Supabase configuration present"
    else
        echo "   ${YELLOW}Warning: Supabase credentials not set${NC}" >&2
        SMOKE_TESTS_PASSED=false
    fi
fi

if [ "$SMOKE_TESTS_PASSED" = true ]; then
    print_success "All smoke tests passed"
fi

if [ "$CALLED_FROM_DEV" = false ]; then
    echo ""
    echo -e "${CYAN}========================================"
    if [ $ERROR_COUNT -eq 0 ]; then
        echo -e "   Installation Complete!"
        echo -e "   ${GREEN}✓ Setup successful${NC}"
    else
        echo -e "   Installation completed with ${YELLOW}$ERROR_COUNT warning(s)${NC}"
    fi
    echo -e "========================================${NC}\n"

    echo ""
    echo -e "${CYAN}================================================================"
    echo -e "                        NEXT STEPS                              "
    echo -e "================================================================${NC}"
    echo ""
    echo -e "${WHITE}  1. Activate the virtual environment:${NC}"
    if [ "$VENV_LAYOUT" = "windows" ]; then
        echo -e "     source .venv/Scripts/activate"
    else
        echo -e "     source .venv/bin/activate"
    fi
    echo ""
    echo -e "${WHITE}  2. Verify collab is installed and working:${NC}"
    echo -e "     collab active"
    echo ""
    echo -e "${WHITE}  3. (Optional) Setup development environment:${NC}"
    echo -e "     ./scripts/setup-dev.sh"
    echo ""
    echo -e "${WHITE}  4. Ensure .env includes real Supabase values:${NC}"
    echo -e "     SUPABASE_URL and SUPABASE_ANON_KEY"
    echo ""
    echo -e "${CYAN}================================================================${NC}"
    echo ""

    if [ $ERROR_COUNT -ne 0 ]; then
        exit 1
    fi
fi
