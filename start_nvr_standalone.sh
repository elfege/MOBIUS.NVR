#!/bin/bash

# start_nvr.sh - NVR System Startup Script
# Starts Flask NVR application with Gunicorn and suppressed access logging

# ./start_nvr.sh           # Foreground mode (default)
# ./start_nvr.sh -d        # Background/daemon mode  
# ./start_nvr.sh --daemon  # Background/daemon mode
# ./start_nvr.sh -h        # Help message

set -e  # Exit on error

LOG_LEVEL="warning"

# Configuration
PROJECT_DIR="/home/elfege/0_MOBIUS.NVR"
VENV_DIR="$PROJECT_DIR/venv"
WORKERS=12
HOST="0.0.0.0"
PORT=5000
DAEMON=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--daemon)
            DAEMON=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  -d, --daemon    Run in background (daemon mode)"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option $1"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Starting NVR System${NC}"
if $DAEMON; then
    echo -e "${BLUE}  Mode: Background (daemon)${NC}"
else
    echo -e "${BLUE}  Mode: Foreground${NC}"
fi
echo -e "${BLUE}========================================${NC}"

# Change to project directory
cd "$PROJECT_DIR" || {
    echo -e "${RED}ERROR: Could not change to project directory: $PROJECT_DIR${NC}"
    exit 1
}

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}ERROR: Virtual environment not found at $VENV_DIR${NC}"
    echo -e "${YELLOW}Please create virtual environment first:${NC}"
    echo -e "${YELLOW}  python3 -m venv venv${NC}"
    exit 1
fi

# Check if virtual environment is already activated
if [[ "$VIRTUAL_ENV" != "$VENV_DIR" ]]; then
    echo -e "${YELLOW}Activating virtual environment...${NC}"
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}ERROR: Could not activate virtual environment${NC}"
        exit 1
    }
else
    echo -e "${GREEN}Virtual environment already activated${NC}"
fi

# Check if gunicorn is installed
if ! command -v gunicorn &> /dev/null; then
    echo -e "${RED}ERROR: Gunicorn not found in virtual environment${NC}"
    echo -e "${YELLOW}Please install gunicorn:${NC}"
    echo -e "${YELLOW}  pip install gunicorn${NC}"
    exit 1
fi

# Check if app.py exists
if [ ! -f "app.py" ]; then
    echo -e "${RED}ERROR: app.py not found in current directory${NC}"
    exit 1
fi

echo -e "${GREEN}Starting NVR application...${NC}"
echo -e "${YELLOW}Configuration:${NC}"
echo -e " Workers: $WORKERS"
echo -e " Bind: $HOST:$PORT"
echo -e " Access logging: Disabled"
echo -e " Error logging: Enabled"
if $DAEMON; then
    echo -e " PID file: $PROJECT_DIR/nvr.pid"
    echo -e " Log file: $PROJECT_DIR/nvr.log"
fi
echo ""

pull_secrets_from_aws UniFi-Camera-Credentials


# Start Gunicorn
if $DAEMON; then
    echo -e "${GREEN}Starting in daemon mode...${NC}"
    python -m gunicorn \
        --workers $WORKERS \
        --bind $HOST:$PORT \
        --access-logfile /dev/null \
        --error-logfile "$PROJECT_DIR/nvr.log" \
        --log-level $LOG_LEVEL \
        --preload \
        --worker-class sync \
        --timeout 120 \
        --daemon \
        --pid "$PROJECT_DIR/nvr.pid" \
        app:app
    
    if [ -f "$PROJECT_DIR/nvr.pid" ]; then
        echo -e "${GREEN}NVR started successfully in background${NC}"
        echo -e "${YELLOW}PID: $(cat $PROJECT_DIR/nvr.pid)${NC}"
        echo -e "${YELLOW}Log file: $PROJECT_DIR/nvr.log${NC}"
        echo -e "${YELLOW}To stop: kill \$(cat $PROJECT_DIR/nvr.pid)${NC}"
    else
        echo -e "${RED}Failed to start NVR in daemon mode${NC}"
        exit 1
    fi
else
    # Foreground mode
    exec python -m gunicorn \
        --workers $WORKERS \
        --bind $HOST:$PORT \
        --access-logfile /dev/null \
        --error-logfile - \
        --log-level $LOG_LEVEL \
        --preload \
        --worker-class sync \
        --timeout 120 \
        app:app
fi