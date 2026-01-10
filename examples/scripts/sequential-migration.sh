#!/bin/bash
#
# Sequential Migration Example
# 
# This script demonstrates sequential VM migration from one node to another.
# VMs are migrated one by one to measure individual migration performance.
#
# Usage:
#   ./sequential-migration.sh <source-node> <target-node> [vm-count]
#
# Example:
#   ./sequential-migration.sh worker-1 worker-2 20
#

set -e

# Configuration
SOURCE_NODE=${1:-"worker-1"}
TARGET_NODE=${2:-"worker-2"}
VM_COUNT=${3:-10}
NAMESPACE_PREFIX="kubevirt-perf-test"
LOG_FILE="sequential-migration-$(date +%Y%m%d-%H%M%S).log"

echo "=========================================="
echo "Sequential Migration Test"
echo "=========================================="
echo "Source Node:  $SOURCE_NODE"
echo "Target Node:  $TARGET_NODE"
echo "VM Count:     $VM_COUNT"
echo "Log File:     $LOG_FILE"
echo "=========================================="
echo ""

# Run migration test
python3 ../migration/measure-vm-migration-time.py \
  --start 1 \
  --end "$VM_COUNT" \
  --source-node "$SOURCE_NODE" \
  --target-node "$TARGET_NODE" \
  --namespace-prefix "$NAMESPACE_PREFIX" \
  --log-file "$LOG_FILE" \
  --log-level INFO

echo ""
echo "=========================================="
echo "Test Complete!"
echo "Log file: $LOG_FILE"
echo "=========================================="

