#!/bin/bash
DESTINATION_ADDRESS=""

# Check if jq is installed
if ! command -v jq &> /dev/null
then
    echo "jq not found. Installing..."
    sudo apt update && sudo apt install -y jq
fi

jq -c '.[]' data.json | while read -r pair; do
    ORIGINAL_ADDRESS=$(echo "$pair" | jq -r '.[0]')
    SIGNATURE=$(echo "$pair" | jq -r '.[1]')

    curl -L -X POST https://scavenger.prod.gd.midnighttge.io/donate_to/${DESTINATION_ADDRESS}/${ORIGINAL_ADDRESS}/${SIGNATURE} -d "{}"
    echo ""
done

