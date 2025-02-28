#!/bin/bash

# Function to cd into a directory or create it if it doesn't exist
cd_or_mkdir() {
    if [ -d "$1" ]; then
        echo "Directory exists: $1"
        cd "$1"
    else
        echo "Creating directory: $1"
        mkdir -p "$1" && cd "$1"
    fi
    echo "Current location: $(pwd)"
}

# Set default JSON file name
JSON_FILE="playlists.json"

# Check if a different JSON file is provided as argument
if [ ! -z "$1" ]; then
    JSON_FILE="$1"
fi

# Check if the file exists
if [ ! -f "$JSON_FILE" ]; then
    echo "Error: File '$JSON_FILE' not found"
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: This script requires jq to be installed."
    echo "Please install jq: sudo apt-get install jq (Ubuntu/Debian) or brew install jq (macOS)"
    exit 1
fi

# Check if spotdl is installed
if ! command -v spotdl &> /dev/null; then
    echo "Error: This script requires spotdl to be installed."
    echo "Please install spotdl: pip install spotdl"
    exit 1
fi

# Store the initial directory to return to later
MASTER_DIR=$(pwd)
echo "Master directory: $MASTER_DIR"

# Process each key-value pair in the JSON file
jq -r 'to_entries[] | "\(.key)|\(.value)"' "$JSON_FILE" | while IFS="|" read -r directory link; do
    # Expand any shell variables or special characters in the path
    evaluated_directory=$(eval echo "$directory")
    
    echo "===================================="
    echo "Processing directory: $evaluated_directory"
    echo "Link: $link"
    
    # CD into the directory (or create and CD if it doesn't exist)
    cd_or_mkdir "$evaluated_directory"
    
    # Run spotdl with the link
    echo "Running: spotdl $link"
    spotdl "$link"
    
    # Return to the master directory
    echo "Returning to master directory"
    cd "$MASTER_DIR"
    echo "===================================="
done

echo "Script completed. Back in: $(pwd)"