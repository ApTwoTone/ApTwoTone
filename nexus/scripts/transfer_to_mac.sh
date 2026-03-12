#!/bin/bash
# Run from Mac: bash scripts/transfer_to_mac.sh [pod_ip] [pod_port]
set -euo pipefail

POD_IP=${1:?Usage: bash scripts/transfer_to_mac.sh [ip] [port]}
POD_PORT=${2:?Usage: bash scripts/transfer_to_mac.sh [ip] [port]}
DEST="$HOME/.nexus/brain_lab/run_14"

echo "Creating local directories..."
mkdir -p "$DEST/models" "$DEST/specialist_exports" "$DEST/training_data" "$DEST/logs"

echo "Pulling GGUF models..."
rsync -avz --progress -e "ssh -p $POD_PORT -o StrictHostKeyChecking=no" \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/exports/gguf/ \
  "$DEST/models/"

echo "Pulling specialist exports..."
rsync -avz --progress -e "ssh -p $POD_PORT -o StrictHostKeyChecking=no" \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/runpod_lab/exports/ \
  "$DEST/specialist_exports/"

echo "Pulling training datasets..."
rsync -avz --progress -e "ssh -p $POD_PORT -o StrictHostKeyChecking=no" \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/coding_data/ \
  "$DEST/training_data/"

echo "Pulling results and logs..."
rsync -avz --progress -e "ssh -p $POD_PORT -o StrictHostKeyChecking=no" \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/specialist_results.json \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/canary_result.json \
  root@"$POD_IP":/workspace/nexus_brain_lab/run_14/gpu_utilization_log.json \
  "$DEST/logs/" || true

echo ""
echo "Transfer complete. Files at: $DEST"
echo "Next step: bash scripts/setup_on_mac.sh"
