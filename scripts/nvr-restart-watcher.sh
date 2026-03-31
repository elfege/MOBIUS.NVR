#!/bin/bash
# nvr-restart-watcher.sh
#
# Minimal systemd daemon that watches a trigger file for restart requests.
# The NVR container writes "reboot" to the trigger file when it needs
# a full restart (start.sh). This watcher picks it up, resets the file,
# and runs start.sh.
#
# Trigger file: /dev/shm/nvr-restart/trigger (tmpfs, shared with container)
# Container writes: echo "reboot" > /dev/shm/nvr-restart/trigger
# Watcher reads, resets to "idle", runs start.sh.

TRIGGER_FILE="/dev/shm/nvr-restart/trigger"
PROJECT_DIR="/home/elfege/0_MOBIUS.NVR"

# Ensure trigger dir and file exist
mkdir -p "$(dirname "$TRIGGER_FILE")"
echo "idle" > "$TRIGGER_FILE"

echo "[nvr-restart-watcher] Watching $TRIGGER_FILE"

while true; do
    if [[ "$(< "$TRIGGER_FILE" 2>/dev/null)" == "reboot" ]]; then
        echo "[nvr-restart-watcher] Restart triggered — running start.sh"
        echo "idle" > "$TRIGGER_FILE"
        cd "$PROJECT_DIR" && ./start.sh
        echo "[nvr-restart-watcher] start.sh completed — resuming watch"
    fi
    sleep 2
done
