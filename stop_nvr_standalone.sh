#!/bin/bash

# stop_nvr.sh - Safe NVR System Stop Script
# Handles both daemon and foreground modes

PROJECT_DIR="/home/elfege/0_NVR"
PID_FILE="$PROJECT_DIR/nvr.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Stopping NVR System...${NC}"

# Function to check if process is running
is_running() {
    local pid=$1
    kill -0 "$pid" 2>/dev/null
}

# Function to wait for process to stop
wait_for_stop() {
    local pid=$1
    local timeout=10
    local count=0
    
    while is_running "$pid" && [ $count -lt $timeout ]; do
        sleep 1
        ((count++))
    done
    
    if is_running "$pid"; then
        echo -e "${RED}Process $pid didn't stop gracefully, force killing...${NC}"
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
}

# Try to stop via PID file first (daemon mode)
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo -e "${YELLOW}Found PID file: $PID${NC}"
    
    if is_running "$PID"; then
        echo -e "${YELLOW}Stopping main process $PID...${NC}"
        kill "$PID"
        wait_for_stop "$PID"
        
        if ! is_running "$PID"; then
            echo -e "${GREEN}Main process stopped${NC}"
        else
            echo -e "${RED}Failed to stop main process${NC}"
        fi
    else
        echo -e "${YELLOW}PID $PID not running${NC}"
    fi
    
    # Remove stale PID file
    rm -f "$PID_FILE"
else
    echo -e "${YELLOW}No PID file found, checking for running processes...${NC}"
fi

# Stop any remaining NVR-related processes
echo -e "${YELLOW}Stopping remaining NVR processes...${NC}"

# Stop gunicorn processes specifically for this project
GUNICORN_PIDS=$(pgrep -f "gunicorn.*app:app" 2>/dev/null || true)
if [ -n "$GUNICORN_PIDS" ]; then
    echo -e "${YELLOW}Stopping gunicorn processes: $GUNICORN_PIDS${NC}"
    echo "$GUNICORN_PIDS" | xargs -r kill
    sleep 2
    # Force kill if still running
    echo "$GUNICORN_PIDS" | xargs -r kill -9 2>/dev/null || true
fi

# Stop FFmpeg processes (more targeted)
FFMPEG_PIDS=$(pgrep -f "ffmpeg.*rtsp://" 2>/dev/null || true)
if [ -n "$FFMPEG_PIDS" ]; then
    echo -e "${YELLOW}Stopping FFmpeg processes: $FFMPEG_PIDS${NC}"
    echo "$FFMPEG_PIDS" | xargs -r kill
    sleep 2
    # Force kill if still running
    echo "$FFMPEG_PIDS" | xargs -r kill -9 2>/dev/null || true
fi

# Check for any remaining processes
REMAINING=$(pgrep -f "gunicorn.*app:app|ffmpeg.*rtsp://" 2>/dev/null || true)
if [ -n "$REMAINING" ]; then
    echo -e "${RED}Some processes may still be running: $REMAINING${NC}"
else
    echo -e "${GREEN}All NVR processes stopped successfully${NC}"
fi

# Optional: Check port 5000
if netstat -tulpn 2>/dev/null | grep -q ":5000 "; then
    echo -e "${YELLOW}Warning: Something is still listening on port 5000${NC}"
fi

echo -e "${GREEN}NVR stop completed${NC}"