#!/bin/bash
# Run on Mac after transfer_to_mac.sh completes
set -euo pipefail

MODELS_DIR="$HOME/.nexus/brain_lab/run_14/models"

echo "Checking Ollama..."
if ! ollama list >/dev/null 2>&1; then
  echo "Starting Ollama..."
  open -a Ollama || true
  sleep 5
fi

echo "Registering Nexus Coder..."
if [ -d "$MODELS_DIR/coding_agent" ] && [ -f "$MODELS_DIR/coding_agent/Modelfile" ]; then
  cd "$MODELS_DIR/coding_agent"
  ollama create nexus-coder -f Modelfile
  ollama run nexus-coder "Confirm you are online. State your role in one sentence." --nowordwrap || true
fi

echo "Registering specialist models..."
for specialist_dir in "$MODELS_DIR"/*/; do
  [ -d "$specialist_dir" ] || continue
  model_name=$(basename "$specialist_dir")
  if [ -f "$specialist_dir/Modelfile" ] && [ "$model_name" != "coding_agent" ]; then
    cd "$specialist_dir"
    ollama create "nexus-$model_name" -f Modelfile || true
    echo "Registered: nexus-$model_name"
  fi
done

echo ""
echo "All models processed. Current nexus* models:"
ollama list | grep -i nexus || true
echo ""
echo "Restart Nexus server to activate brain endpoints:"
echo "cd ~/nexus && source venv/bin/activate && python server.py"
