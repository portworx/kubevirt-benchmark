#!/bin/bash
#
# Node Evacuation Example
#
# This script demonstrates evacuating all VMs from a node before maintenance.
# VMs are migrated to any available nodes in the cluster.
#
# Two modes:
#   1. Evacuate specific node (provide node name)
#   2. Auto-select busiest node (use "auto" as node name)
#
# Usage:
#   ./evacuation-scenario.sh <source-node|auto> [vm-count] [concurrency]
#
# Examples:
#   ./evacuation-scenario.sh worker-3 100 20
#   ./evacuation-scenario.sh auto 100 20
#

set -e

# Configuration
SOURCE_NODE=${1:-"worker-1"}
VM_COUNT=${2:-100}
CONCURRENCY=${3:-20}
NAMESPACE_PREFIX="kubevirt-perf-test"

# Check if auto-select mode
if [[ "$SOURCE_NODE" == "auto" ]]; then
    AUTO_SELECT="--auto-select-busiest"
    LOG_FILE="evacuation-auto-$(date +%Y%m%d-%H%M%S).log"

    echo "=========================================="
    echo "Node Evacuation Test (Auto-Select)"
    echo "=========================================="
    echo "Mode:         Auto-select busiest node"
    echo "VM Count:     $VM_COUNT"
    echo "Concurrency:  $CONCURRENCY"
    echo "Log File:     $LOG_FILE"
    echo "=========================================="
    echo ""
    echo "WARNING: This will auto-select and evacuate the busiest node"
    echo ""
else
    AUTO_SELECT=""
    LOG_FILE="evacuation-${SOURCE_NODE}-$(date +%Y%m%d-%H%M%S).log"

    echo "=========================================="
    echo "Node Evacuation Test"
    echo "=========================================="
    echo "Source Node:  $SOURCE_NODE"
    echo "VM Count:     $VM_COUNT"
    echo "Concurrency:  $CONCURRENCY"
    echo "Log File:     $LOG_FILE"
    echo "=========================================="
    echo ""
    echo "WARNING: This will evacuate all VMs from $SOURCE_NODE"
    echo ""
fi

read -p "Continue? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "Starting evacuation..."
echo ""

# Build command
CMD="python3 ../migration/measure-vm-migration-time.py \
  --start 1 \
  --end $VM_COUNT \
  --evacuate \
  --concurrency $CONCURRENCY \
  --namespace-prefix $NAMESPACE_PREFIX \
  --log-file $LOG_FILE \
  --log-level INFO"

# Add source node or auto-select flag
if [[ "$SOURCE_NODE" == "auto" ]]; then
    CMD="$CMD --auto-select-busiest"
else
    CMD="$CMD --source-node $SOURCE_NODE"
fi

# Run evacuation
eval $CMD

echo ""
echo "=========================================="
echo "Evacuation Complete!"
echo "Log file: $LOG_FILE"
echo ""
if [[ "$SOURCE_NODE" != "auto" ]]; then
    echo "Node $SOURCE_NODE is now ready for maintenance."
fi
echo "=========================================="

