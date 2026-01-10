#!/bin/bash
#
# Round-Robin Migration Example
# 
# This script demonstrates round-robin VM migration across all available nodes.
# VMs are distributed evenly across the cluster for load balancing.
#
# Usage:
#   ./round-robin-migration.sh [vm-count] [concurrency]
#
# Example:
#   ./round-robin-migration.sh 100 20
#

set -e

# Configuration
VM_COUNT=${1:-100}
CONCURRENCY=${2:-20}
NAMESPACE_PREFIX="kubevirt-perf-test"
LOG_FILE="round-robin-migration-$(date +%Y%m%d-%H%M%S).log"

echo "=========================================="
echo "Round-Robin Migration Test"
echo "=========================================="
echo "VM Count:     $VM_COUNT"
echo "Concurrency:  $CONCURRENCY"
echo "Log File:     $LOG_FILE"
echo "=========================================="
echo ""

# Show available nodes
echo "Available worker nodes:"
kubectl get nodes -l node-role.kubernetes.io/worker= --no-headers | awk '{print "  - " $1}'
echo ""

# Run round-robin migration
python3 ../migration/measure-vm-migration-time.py \
  --start 1 \
  --end "$VM_COUNT" \
  --round-robin \
  --concurrency "$CONCURRENCY" \
  --namespace-prefix "$NAMESPACE_PREFIX" \
  --log-file "$LOG_FILE" \
  --log-level INFO

echo ""
echo "=========================================="
echo "Test Complete!"
echo "Log file: $LOG_FILE"
echo ""
echo "VM distribution across nodes:"
kubectl get vmi -A -o wide | grep "$NAMESPACE_PREFIX" | awk '{print $7}' | sort | uniq -c
echo "=========================================="

