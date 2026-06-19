#!/bin/bash
# Template Application Helper Script
#
# This script helps apply template variables to VM template files
#
# Usage:
#   ./apply_template.sh --template vm-template.yaml --output my-vm.yaml \
#     --vm-name my-vm --storage-class YOUR-STORAGE-CLASS

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default values
TEMPLATE_FILE="$REPO_ROOT/examples/vm-templates/vm-template.yaml"
OUTPUT_FILE=""
VM_NAME="rhel-9-vm"
STORAGE_CLASS_NAME=""
DATASOURCE_NAME="rhel9"
DATASOURCE_NAMESPACE="openshift-virtualization-os-images"
STORAGE_SIZE="30Gi"
VM_MEMORY="2048M"
VM_CPU_CORES="1"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Apply template variables to KubeVirt VM template files.

OPTIONS:
    -t, --template FILE          Template file path (default: ../examples/vm-templates/vm-template.yaml)
    -o, --output FILE            Output file path (required)
    -n, --vm-name NAME           VM name (default: rhel-9-vm)
    -s, --storage-class NAME     Storage class name (required)
    -d, --datasource NAME        DataSource name (default: rhel9)
    --datasource-namespace NS    DataSource namespace (default: openshift-virtualization-os-images)
    --storage-size SIZE          Storage size (default: 30Gi)
    --memory SIZE                VM memory (default: 2048M)
    --cpu-cores NUM              Number of CPU cores (default: 1)
    -h, --help                   Show this help message

EXAMPLES:
    # Basic usage with custom VM name and storage class
    $0 -o my-vm.yaml -n my-vm -s YOUR-STORAGE-CLASS

    # Full customization
    $0 -o custom-vm.yaml \\
        -n custom-vm \\
        -s YOUR-STORAGE-CLASS \\
        -d fedora \\
        --storage-size 50Gi \\
        --memory 4Gi \\
        --cpu-cores 2

    # Apply and create VM directly
    $0 -o /tmp/vm.yaml -n test-vm -s YOUR-STORAGE-CLASS && kubectl apply -f /tmp/vm.yaml -n test-namespace

EOF
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--template)
            TEMPLATE_FILE="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        -n|--vm-name)
            VM_NAME="$2"
            shift 2
            ;;
        -s|--storage-class)
            STORAGE_CLASS_NAME="$2"
            shift 2
            ;;
        -d|--datasource)
            DATASOURCE_NAME="$2"
            shift 2
            ;;
        --datasource-namespace)
            DATASOURCE_NAMESPACE="$2"
            shift 2
            ;;
        --storage-size)
            STORAGE_SIZE="$2"
            shift 2
            ;;
        --memory)
            VM_MEMORY="$2"
            shift 2
            ;;
        --cpu-cores)
            VM_CPU_CORES="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Error: Unknown option $1${NC}"
            usage
            ;;
    esac
done

# Validate required arguments
if [ -z "$OUTPUT_FILE" ]; then
    echo -e "${RED}Error: Output file is required (-o/--output)${NC}"
    usage
fi

if [ -z "$STORAGE_CLASS_NAME" ]; then
    echo -e "${RED}Error: Storage class is required (-s/--storage-class)${NC}"
    usage
fi

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo -e "${RED}Error: Template file not found: $TEMPLATE_FILE${NC}"
    exit 1
fi

# Print configuration
echo -e "${GREEN}Applying template variables...${NC}"
echo "Template file:        $TEMPLATE_FILE"
echo "Output file:          $OUTPUT_FILE"
echo "VM Name:              $VM_NAME"
echo "Storage Class:        $STORAGE_CLASS_NAME"
echo "DataSource:           $DATASOURCE_NAME"
echo "DataSource Namespace: $DATASOURCE_NAMESPACE"
echo "Storage Size:         $STORAGE_SIZE"
echo "VM Memory:            $VM_MEMORY"
echo "VM CPU Cores:         $VM_CPU_CORES"
echo ""

# Apply template variables using sed
cat "$TEMPLATE_FILE" | \
    sed "s/{{VM_NAME}}/$VM_NAME/g" | \
    sed "s/{{STORAGE_CLASS_NAME}}/$STORAGE_CLASS_NAME/g" | \
    sed "s/{{DATASOURCE_NAME}}/$DATASOURCE_NAME/g" | \
    sed "s/{{DATASOURCE_NAMESPACE}}/$DATASOURCE_NAMESPACE/g" | \
    sed "s/{{STORAGE_SIZE}}/$STORAGE_SIZE/g" | \
    sed "s/{{VM_MEMORY}}/$VM_MEMORY/g" | \
    sed "s/{{VM_CPU_CORES}}/$VM_CPU_CORES/g" \
    > "$OUTPUT_FILE"

# Check if output file was created successfully
if [ -f "$OUTPUT_FILE" ]; then
    echo -e "${GREEN}✓ Template applied successfully!${NC}"
    echo -e "Output saved to: ${YELLOW}$OUTPUT_FILE${NC}"
    echo ""
    echo "To apply this VM configuration:"
    echo "  kubectl apply -f $OUTPUT_FILE -n <namespace>"
    exit 0
else
    echo -e "${RED}✗ Failed to create output file${NC}"
    exit 1
fi
