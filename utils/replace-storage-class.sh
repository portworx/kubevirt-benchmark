#!/bin/bash
# Replace Storage Class in All VM Template YAML Files
#
# This script replaces the storageClassName in all VM template YAML files
# in the examples/vm-templates directory.
#
# Usage:
#   ./replace-storage-class.sh <new-storage-class-name>
#
# Example:
#   ./replace-storage-class.sh my-storage-class

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$REPO_ROOT/examples/vm-templates"

# Function to print colored messages
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 <new-storage-class-name> [options]

Replace storageClassName in all VM template YAML files.

Arguments:
    <new-storage-class-name>    The new storage class name to use

Options:
    --dry-run                   Show what would be changed without making changes
    --backup                    Create backup files (.bak) before modifying
    --file <path>               Only modify specific file instead of all templates
    -h, --help                  Show this help message

Examples:
    # Replace storage class in all templates
    $0 my-storage-class

    # Dry run to see what would change
    $0 my-storage-class --dry-run

    # Replace with backup
    $0 my-storage-class --backup

    # Replace in specific file only
    $0 my-storage-class --file examples/vm-templates/rhel9-vm-datasource.yaml

EOF
}

# Parse arguments
NEW_STORAGE_CLASS=""
DRY_RUN=false
CREATE_BACKUP=false
SPECIFIC_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_usage
            exit 0
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --backup)
            CREATE_BACKUP=true
            shift
            ;;
        --file)
            SPECIFIC_FILE="$2"
            shift 2
            ;;
        -*)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
        *)
            if [ -z "$NEW_STORAGE_CLASS" ]; then
                NEW_STORAGE_CLASS="$1"
            else
                print_error "Too many arguments"
                show_usage
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate arguments
if [ -z "$NEW_STORAGE_CLASS" ]; then
    print_error "Storage class name is required"
    show_usage
    exit 1
fi

# Function to replace storage class in a file
replace_in_file() {
    local file="$1"
    local storage_class="$2"
    local dry_run="$3"
    local create_backup="$4"

    if [ ! -f "$file" ]; then
        print_error "File not found: $file"
        return 1
    fi

    # Check if file contains storageClassName
    if ! grep -q "storageClassName:" "$file"; then
        print_warning "No storageClassName found in: $file"
        return 0
    fi

    # Get current storage class
    current_sc=$(grep "storageClassName:" "$file" | head -1 | sed 's/.*storageClassName: *//' | tr -d '"' | tr -d "'")

    if [ "$current_sc" = "$storage_class" ]; then
        print_info "Already using '$storage_class' in: $(basename $file)"
        return 0
    fi

    if [ "$dry_run" = true ]; then
        print_info "Would change '$current_sc' → '$storage_class' in: $(basename $file)"
        return 0
    fi

    # Create backup if requested
    if [ "$create_backup" = true ]; then
        cp "$file" "$file.bak"
        print_info "Created backup: $file.bak"
    fi

    # Replace storage class
    # Use different sed syntax for macOS vs Linux
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/storageClassName: .*/storageClassName: $storage_class/" "$file"
    else
        sed -i "s/storageClassName: .*/storageClassName: $storage_class/" "$file"
    fi

    print_success "Updated '$current_sc' → '$storage_class' in: $(basename $file)"
}

# Main execution
echo ""
print_info "Storage Class Replacement Tool"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$DRY_RUN" = true ]; then
    print_warning "DRY RUN MODE - No files will be modified"
    echo ""
fi

# Process files
if [ -n "$SPECIFIC_FILE" ]; then
    # Process specific file
    print_info "Processing specific file: $SPECIFIC_FILE"
    echo ""
    replace_in_file "$SPECIFIC_FILE" "$NEW_STORAGE_CLASS" "$DRY_RUN" "$CREATE_BACKUP"
else
    # Process all YAML files in templates directory
    print_info "Processing all VM templates in: $TEMPLATES_DIR"
    echo ""

    file_count=0
    for file in "$TEMPLATES_DIR"/*.yaml; do
        if [ -f "$file" ]; then
            replace_in_file "$file" "$NEW_STORAGE_CLASS" "$DRY_RUN" "$CREATE_BACKUP"
            ((file_count++))
        fi
    done

    echo ""
    print_success "Processed $file_count template file(s)"
fi

echo ""
if [ "$DRY_RUN" = true ]; then
    print_info "This was a dry run. Run without --dry-run to apply changes."
elif [ "$CREATE_BACKUP" = true ]; then
    print_info "Backup files created with .bak extension"
    print_info "To restore: for f in $TEMPLATES_DIR/*.bak; do mv \"\$f\" \"\${f%.bak}\"; done"
fi

echo ""
print_success "Done!"
echo ""
