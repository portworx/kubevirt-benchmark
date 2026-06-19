#!/bin/bash
# Installation script for virtbench CLI

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Installation mode
USE_VENV=false
FORCE_SYSTEM=false

# Print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_banner() {
    echo -e "${CYAN}$1${NC}"
}

# Check if Python3 is installed and meets minimum version requirement
check_python() {
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 is not installed. virtbench requires Python 3.8+."
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    print_info "Found Python version: $PYTHON_VERSION"

    # Check Python version
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    # Check minimum version requirement (Python 3.8+)
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
        print_error "Python 3.8+ is required. Found Python $PYTHON_VERSION"
        print_error "Please upgrade your Python installation."
        exit 1
    fi

    # Check if Python version is 3.11+ (PEP 668 enforcement)
    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 11 ] && [ "$FORCE_SYSTEM" = false ]; then
        print_warning "Python 3.11+ detected. Using virtual environment to avoid PEP 668 issues."
        USE_VENV=true
    fi
}

# Check if pip3 is installed
check_pip() {
    if ! command -v pip3 &> /dev/null; then
        print_error "pip3 is not installed. Please install pip3."
        print_info "Visit: https://pip.pypa.io/en/stable/installation/"
        exit 1
    fi

    PIP_VERSION=$(pip3 --version | awk '{print $2}')
    print_info "Found pip version: $PIP_VERSION"
}

# Create virtual environment
create_venv() {
    if [ "$USE_VENV" = true ]; then
        print_info "Creating virtual environment..."

        # Check if venv exists and is valid (has activate script)
        if [ -d "venv" ]; then
            if [ -f "venv/bin/activate" ]; then
                print_warning "Virtual environment already exists. Reusing it."
            else
                print_warning "Virtual environment exists but is corrupted (missing activate script). Recreating..."
                rm -rf venv
                if python3 -m venv venv; then
                    print_info "✓ Virtual environment recreated"
                else
                    print_error "Failed to create virtual environment!"
                    print_info "Try installing: apt install python3-venv"
                    exit 1
                fi
            fi
        else
            if python3 -m venv venv; then
                print_info "✓ Virtual environment created"
            else
                print_error "Failed to create virtual environment!"
                print_info "Try installing: apt install python3-venv"
                exit 1
            fi
        fi

        # Verify activate script exists before sourcing
        if [ ! -f "venv/bin/activate" ]; then
            print_error "Virtual environment creation failed - activate script not found!"
            print_info "This may indicate python3-venv is not properly installed."
            print_info "Try: apt install python3-venv python3-full"
            exit 1
        fi

        # Activate virtual environment
        source venv/bin/activate
        print_info "✓ Virtual environment activated"
    fi
}

# Install Python dependencies
install_python_deps() {
    print_info "Installing Python dependencies from requirements.txt..."
    if [ -f "requirements.txt" ]; then
        PIP_CMD="pip3"
        if [ "$USE_VENV" = true ]; then
            PIP_CMD="pip"
        fi

        if $PIP_CMD install -r requirements.txt; then
            print_info "✓ Python dependencies installed successfully"
        else
            print_error "Failed to install Python dependencies!"
            if [ "$USE_VENV" = false ]; then
                print_info "Try one of these options:"
                echo "  1. Run with virtual environment: ./install.sh --venv"
                echo "  2. Use system packages (risky): ./install.sh --system"
                echo "  3. Manual install: pip3 install --user -r requirements.txt"
            fi
            exit 1
        fi
    else
        print_error "requirements.txt not found!"
        exit 1
    fi
}

# Install virtbench CLI
install_virtbench_cli() {
    print_info "Installing virtbench CLI..."

    PIP_CMD="pip3"
    if [ "$USE_VENV" = true ]; then
        PIP_CMD="pip"
    fi

    # Install in development mode (editable install)
    if $PIP_CMD install -e . ; then
        print_info "✓ virtbench CLI installed successfully"
    else
        print_error "Failed to install virtbench CLI"
        print_error "Please run manually: $PIP_CMD install -e ."
        exit 1
    fi
}

# Verify installation
verify_installation() {
    print_info "Verifying installation..."

    if command -v virtbench &> /dev/null; then
        print_info "✓ virtbench command is available"
        echo ""
        virtbench --version
        return 0
    else
        print_warning "virtbench command not found in PATH"
        print_info "You may need to add ~/.local/bin to your PATH"
        print_info "Add this to your ~/.bashrc or ~/.zshrc:"
        echo ""
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        return 1
    fi
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --venv)
                USE_VENV=true
                shift
                ;;
            --system)
                FORCE_SYSTEM=true
                USE_VENV=false
                shift
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --venv      Force installation in a virtual environment"
                echo "  --system    Force system-wide installation (may require --break-system-packages)"
                echo "  --help      Show this help message"
                echo ""
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
}

# Main installation
main() {
    echo ""
    print_banner "================================================================================"
    print_banner "  KubeVirt Benchmark Suite - virtbench CLI Installation"
    print_banner "================================================================================"
    echo ""

    # Check prerequisites
    print_info "Checking prerequisites..."
    check_python
    check_pip
    echo ""

    # Create virtual environment if needed
    if [ "$USE_VENV" = true ]; then
        create_venv
        echo ""
    fi

    # Install dependencies
    install_python_deps
    echo ""

    # Install virtbench CLI
    install_virtbench_cli
    echo ""

    # Verify installation
    verify_installation
    echo ""

    # Success message
    print_banner "================================================================================"
    print_info "✓ Installation complete!"
    print_banner "================================================================================"
    echo ""

    if [ "$USE_VENV" = true ]; then
        print_warning "Virtual environment is being used."
        print_info "To use virtbench, activate the virtual environment first:"
        echo ""
        echo "    source venv/bin/activate"
        echo ""
        print_info "Then you can run virtbench commands:"
    else
        print_info "Get started with:"
    fi

    echo ""
    echo "    virtbench --help                    # Show help"
    echo "    virtbench version                   # Show version"
    echo "    virtbench validate-cluster          # Validate your cluster"
    echo ""
    print_info "Example commands:"
    echo ""
    echo "    # Run datasource clone test"
    echo "    virtbench datasource-clone --start 1 --end 10 --storage-class YOUR-STORAGE-CLASS"
    echo ""
    echo "    # Run chaos benchmark"
    echo "    virtbench chaos-benchmark --storage-class YOUR-STORAGE-CLASS --concurrency 2 --vms 5"
    echo ""
    print_info "For more information, see the README.md file"
    echo ""
}

# Parse arguments and run main installation
parse_args "$@"
main
