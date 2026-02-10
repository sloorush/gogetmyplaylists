#!/bin/bash

# =============================================================================
# Spotify Playlist Sync Script
# Uses spotdl sync to efficiently update playlists (only downloads new songs)
# =============================================================================

set -o pipefail

# Configuration
DELAY_SECONDS=5          # Delay between playlists to avoid rate limiting
MAX_RETRIES=2            # Number of retries for failed playlists
LOG_DIR="$HOME/Music/Spotify/.logs"
JSON_FILE="playlists.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Arrays to track results
FAILED_PLAYLISTS=()
FAILED_LINKS=()
SUCCESSFUL=0
SKIPPED=0

# Function to log with timestamp
log() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "$message"
    echo "[$timestamp] $(echo -e "$message" | sed 's/\x1b\[[0-9;]*m//g')" >> "$LOG_FILE"
}

# Function to cd into a directory or create it if it doesn't exist
cd_or_mkdir() {
    if [ -d "$1" ]; then
        cd "$1"
    else
        log "${YELLOW}Creating directory: $1${NC}"
        mkdir -p "$1" && cd "$1"
    fi
}

# Function to sync a single playlist with retry logic
sync_playlist() {
    local directory="$1"
    local link="$2"
    local attempt=1

    while [ $attempt -le $MAX_RETRIES ]; do
        if [ $attempt -gt 1 ]; then
            log "${YELLOW}Retry attempt $attempt/$MAX_RETRIES${NC}"
            sleep 3
        fi

        # Use spotdl sync for efficient updates
        if spotdl sync "$link" --save-file "$(basename "$directory").sync.spotdl" 2>&1 | tee -a "$LOG_FILE"; then
            return 0
        fi

        attempt=$((attempt + 1))
    done

    return 1
}

# Check if a different JSON file is provided as argument
if [ ! -z "$1" ]; then
    JSON_FILE="$1"
fi

# Check if the file exists
if [ ! -f "$JSON_FILE" ]; then
    echo -e "${RED}Error: File '$JSON_FILE' not found${NC}"
    exit 1
fi

# Check dependencies
for cmd in jq spotdl; do
    if ! command -v $cmd &> /dev/null; then
        echo -e "${RED}Error: This script requires $cmd to be installed.${NC}"
        exit 1
    fi
done

# Setup logging
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/sync_$(date '+%Y%m%d_%H%M%S').log"
LATEST_LOG="$LOG_DIR/latest.log"

# Store the initial directory
MASTER_DIR=$(pwd)

# Count total playlists
TOTAL=$(jq 'length' "$JSON_FILE")
CURRENT=0

log "${BLUE}========================================${NC}"
log "${BLUE}Spotify Playlist Sync Started${NC}"
log "${BLUE}Total playlists: $TOTAL${NC}"
log "${BLUE}Log file: $LOG_FILE${NC}"
log "${BLUE}========================================${NC}"

# Process each playlist
jq -r 'to_entries[] | "\(.key)|\(.value)"' "$JSON_FILE" | while IFS="|" read -r directory link; do
    CURRENT=$((CURRENT + 1))

    # Expand any shell variables or special characters in the path
    evaluated_directory=$(eval echo "$directory")
    playlist_name=$(basename "$evaluated_directory")

    log ""
    log "${BLUE}[$CURRENT/$TOTAL] ${NC}${GREEN}$playlist_name${NC}"
    log "Directory: $evaluated_directory"
    log "Link: $link"

    # CD into the directory (or create and CD if it doesn't exist)
    cd_or_mkdir "$evaluated_directory"

    # Sync the playlist
    if sync_playlist "$evaluated_directory" "$link"; then
        log "${GREEN}✓ Successfully synced: $playlist_name${NC}"
        # Write success to a temp file since we're in a subshell
        echo "SUCCESS" >> "$LOG_DIR/.sync_status_$$"
    else
        log "${RED}✗ Failed to sync: $playlist_name${NC}"
        echo "FAILED|$evaluated_directory|$link" >> "$LOG_DIR/.sync_status_$$"
    fi

    # Return to the master directory
    cd "$MASTER_DIR"

    # Add delay between playlists (skip on last playlist)
    if [ "$CURRENT" -lt "$TOTAL" ]; then
        log "${YELLOW}Waiting ${DELAY_SECONDS}s before next playlist...${NC}"
        sleep "$DELAY_SECONDS"
    fi
done

# Read results from temp file
if [ -f "$LOG_DIR/.sync_status_$$" ]; then
    SUCCESSFUL=$(grep -c "^SUCCESS" "$LOG_DIR/.sync_status_$$" 2>/dev/null || echo 0)
    FAILED_COUNT=$(grep -c "^FAILED" "$LOG_DIR/.sync_status_$$" 2>/dev/null || echo 0)

    # Collect failed playlists for summary
    while IFS="|" read -r status dir link; do
        if [ "$status" = "FAILED" ]; then
            FAILED_PLAYLISTS+=("$dir")
            FAILED_LINKS+=("$link")
        fi
    done < <(grep "^FAILED" "$LOG_DIR/.sync_status_$$" 2>/dev/null)

    rm -f "$LOG_DIR/.sync_status_$$"
else
    SUCCESSFUL=0
    FAILED_COUNT=0
fi

# Summary
log ""
log "${BLUE}========================================${NC}"
log "${BLUE}Sync Complete!${NC}"
log "${GREEN}Successful: $SUCCESSFUL${NC}"
log "${RED}Failed: $FAILED_COUNT${NC}"
log "${BLUE}========================================${NC}"

# List failed playlists if any
if [ ${#FAILED_PLAYLISTS[@]} -gt 0 ]; then
    log ""
    log "${RED}Failed playlists:${NC}"
    for i in "${!FAILED_PLAYLISTS[@]}"; do
        log "  - $(basename "${FAILED_PLAYLISTS[$i]}")"
        log "    ${FAILED_LINKS[$i]}"
    done
    log ""
    log "${YELLOW}To retry failed playlists manually:${NC}"
    for i in "${!FAILED_PLAYLISTS[@]}"; do
        log "  cd \"${FAILED_PLAYLISTS[$i]}\" && spotdl sync \"${FAILED_LINKS[$i]}\""
    done
fi

# Create/update symlink to latest log
ln -sf "$LOG_FILE" "$LATEST_LOG"
log ""
log "Full log saved to: $LOG_FILE"
log "Latest log symlink: $LATEST_LOG"
