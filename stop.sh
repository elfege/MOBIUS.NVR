#!/bin/bash
# stop.sh - Stop Unified NVR container

# set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================="
echo "  Unified NVR - Container Shutdown"
echo "=========================================="
echo ""

cd ~/0_MOBIUS.NVR

echo "Stopping unified-nvr container..."
docker compose down

# Clean up streams directory - removes ALL camera stream segments
echo "Cleaning streams directory..."

# Check if streams directory exists and has content
if [ -d ~/0_MOBIUS.NVR/streams ] && [ "$(ls -A ~/0_MOBIUS.NVR/streams)" ]; then
	# Use rsync with empty dir (fastest for directories with thousands of files)
	# This is more efficient than rm -rf for large file counts because:
	# - rsync uses optimized directory traversal
	# - Doesn't build full argument lists in memory
	# - Handles "argument list too long" errors gracefully

	mkdir -p ~/empty_dir

	# Recursively sync empty directory, deleting everything in target
	rsync -a --delete ~/empty_dir/ ~/0_MOBIUS.NVR/streams/

	# Clean up temporary directory
	rmdir ~/empty_dir

	echo -e "${GREEN}✓ Streams directory cleaned${NC}"
else
	echo -e "${YELLOW}⚠ Streams directory is already empty or doesn't exist${NC}"
fi
