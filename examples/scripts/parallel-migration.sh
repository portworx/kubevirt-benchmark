#!/bin/bash
#
# Parallel Migration Example
# 
# This script demonstrates parallel VM migration with configurable concurrency.
# Multiple VMs are migrated simultaneously to test performance under load.
#
# Usage:
#   ./parallel-migration.sh <source-node> <target-node> [vm-count] [concurrency]
#
# Example:
#   ./parallel-migration.sh worker-1 worker-2 50 10
#

set -e

# Configuration
SOURCE_NODE=${1:-"worker-1"}
TARGET_NODE=${2:-"worker-2"}
VM_COUNT=${3:-50}
CONCURRENCY=${4:-10}
NAMESPACE_PREFIX="kubevirt-perf-test"
LOG_FILE="parallel-migration-$(date +%Y%m%d-%H%M%S).log"

echo "=========================================="
echo "Parallel Migration Test"
echo "=========================================="
echo "Source Node:  $SOURCE_NODE"
echo "Target Node:  $TARGET_NODE"
echo "VM Count:     $VM_COUNT"
echo "Concurrency:  $CONCURRENCY"
echo "Log File:     $LOG_FILE"
echo "=========================================="
echo ""

# Run migration test
python3 ../migration/measure-vm-migration-time.py \
  --start 1 \
  --end "$VM_COUNT" \
  --source-node "$SOURCE_NODE" \
  --target-node "$TARGET_NODE" \
  --parallel \
  --concurrency "$CONCURRENCY" \
  --namespace-prefix "$NAMESPACE_PREFIX" \
  --log-file "$LOG_FILE" \
  --log-level INFO

echo ""
echo "=========================================="
echo "Test Complete!"
echo "Log file: $LOG_FILE"
echo "=========================================="

