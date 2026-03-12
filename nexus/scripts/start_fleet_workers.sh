#!/bin/bash
# start_fleet_workers.sh — Launch background workers for the AI Fleet.

COUNT=${1:-5}
TIER=${2:-3}

echo "Starting $COUNT Nexus workers at tier $TIER..."

for i in $(seq 1 $COUNT); do
    NAME="worker-$i"
    echo "Launching $NAME..."
    python3 scripts/worker_runner.py --name "$NAME" --tier $TIER > ~/.nexus/process_logs/$NAME.log 2>&1 &
done

echo "Workers launched. Check status with: python3 scripts/fleet_status.py"
