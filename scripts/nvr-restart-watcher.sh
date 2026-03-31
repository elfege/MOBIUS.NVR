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
# Watcher reads, resets to "last triggered: $(date)", runs start.sh.

TRIGGER_FILE="/dev/shm/nvr-restart/trigger"
PROJECT_DIR="$HOME/0_MOBIUS.NVR"
NVR_START_LOGS="$PROJECT_DIR/restart_from_app.log"
LAST_TRIGGER_TIME="never"

# Ensure trigger dir and file exist
mkdir -p "$(dirname "$TRIGGER_FILE")"

. "$HOME/.env.colors" &>/dev/null || {
	RED="\033[38;5;1m"
	ACCENT_YELLOW="\033[38;5;226m"
	GREEN="\033[38;5;2m"
	CYAN="\033[38;5;6m"
	BLUE="\033[38;5;4m"
	NC='\033[0m'

	echo "================================================================================================="
	echo -e "${RED}[nvr-restart-watcher] Failed to load color definitions from $HOME/.env.colors.${NC}"
	echo "================================================================================================="

}

echo -e "${ACCENT_YELLOW}[nvr-restart-watcher] Watching $TRIGGER_FILE${NC}"

# Initialize trigger file with service start time
Service_Start_Time=$(date)
echo "Service started: $Service_Start_Time" >"$TRIGGER_FILE"

start=$(date +%s)
while true; do
	if [[ "$(cat "$TRIGGER_FILE" 2>/dev/null)" == "reboot" ]]; then
		echo -e "${ACCENT_YELLOW}[nvr-restart-watcher] Restart triggered — running start.sh${NC}" >>"$HOME/log.log"
		LAST_TRIGGER_TIME=$(date)
		echo "[nvr-restart-watcher] Started: $Service_Start_Time | triggered: $LAST_TRIGGER_TIME" | tee "$TRIGGER_FILE"
		(
			pkill -f ./start.sh || true
			sleep 2
			cd "$PROJECT_DIR" && ./start.sh || {
				echo -e "${RED}[nvr-restart-watcher] start.sh failed${NC}" >>"$HOME/log.log" 2>&1
				echo -e "${RED}[nvr-restart-watcher] Check start.sh logs for details${NC}" >>"$HOME/log.log" 2>&1
				exit 1
			}
		) >>"$NVR_START_LOGS" 2>&1 &
	
		if [[ $? -ne 0 ]]; then
			echo -e "${RED}[nvr-restart-watcher] start.sh execution failed${NC}" >>"$HOME/log.log" 2>&1
			echo -e "${RED}[nvr-restart-watcher] Check start.sh logs for details${NC}" >>"$HOME/log.log" 2>&1
			continue
		fi
		echo -e "${GREEN}[nvr-restart-watcher] start.sh completed successfully${NC}" >>"$HOME/log.log" 2>&1
		echo -e "${BLUE}[nvr-restart-watcher] start.sh completed — resuming watch${NC}" >>"$HOME/log.log" 2>&1
	fi
	if (($(date +%s) - start > 3)); then
		if [[ ! $(cat "$TRIGGER_FILE" 2>/dev/null) == *triggered* ]]; then
			# No trigger, write status with timestamp
			echo -e "${GREEN}[nvr-restart-watcher] ${CYAN}Started:${NC} $Service_Start_Time | ${CYAN}last triggered:${NC} $LAST_TRIGGER_TIME" | tee "$TRIGGER_FILE"
		fi
		# echo -e "${GREEN}[nvr-restart-watcher] monitoring.....${NC}"
		cat "$TRIGGER_FILE"
		start=$(date +%s)
	fi
	sleep 2
done
