# Installation Guide

This guide will help you install the virtbench CLI tool for running KubeVirt performance benchmarks.

## Prerequisites

Before installing virtbench, ensure you have the following:

- **Python 3.8 or higher** (required)
- **pip3** (Python package manager)
- **kubectl** CLI configured with cluster access
- **Git** (to clone the repository)

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/portworx/kubevirt-benchmark.git
cd kubevirt-benchmark
```

### 2. Verify Python Version

Ensure you have Python 3.8 or higher installed:

```bash
python3 --version
```

If your Python version is below 3.8, please upgrade before proceeding.

### 3. Verify pip Installation

Check that pip3 is installed:

```bash
pip3 --version
```

If pip3 is not installed, visit [pip installation guide](https://pip.pypa.io/en/stable/installation/).

### 4. Run the Installation Script

The easiest way to install virtbench is using the provided installation script:

```bash
./install.sh
```

This script will:
- Check Python and pip versions
- Install required Python dependencies
- Install the virtbench CLI tool
- Verify the installation

### 5. Installation Options

#### Standard Installation

For most users, the standard installation is recommended:

```bash
./install.sh
```

#### Virtual Environment Installation

If you're using Python 3.11+ or prefer to use a virtual environment:

```bash
./install.sh --venv
```

This creates a virtual environment in the `venv/` directory. To use virtbench after installation:

```bash
source venv/bin/activate
virtbench --version
```

#### System-Wide Installation (Advanced)

To force system-wide installation (not recommended for Python 3.11+):

```bash
./install.sh --system
```

### 6. Verify Installation

After installation, verify that virtbench is available:

```bash
virtbench --version
```

You should see output similar to:

```
virtbench version 2.0.0
```

### 7. Enable Shell Completion (Optional)

Enable tab completion for virtbench commands:

**For Bash:**
```bash
echo 'eval "$(_VIRTBENCH_COMPLETE=bash_source virtbench)"' >> ~/.bashrc
source ~/.bashrc
```

**For Zsh:**
```bash
echo 'eval "$(_VIRTBENCH_COMPLETE=zsh_source virtbench)"' >> ~/.zshrc
source ~/.zshrc
```

### 8. Verify kubectl Access

Ensure kubectl is configured and you have access to your cluster:

```bash
kubectl get nodes
```

### 9. Validate Cluster Prerequisites

Run the cluster validation command to ensure your cluster is ready:

```bash
virtbench validate-cluster --storage-class YOUR-STORAGE-CLASS
```

Replace `YOUR-STORAGE-CLASS` with the name of your storage class.

## Troubleshooting

### virtbench command not found

If the `virtbench` command is not found after installation, you may need to add `~/.local/bin` to your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add this line to your `~/.bashrc` or `~/.zshrc` to make it permanent.

### Python version too old

If you see an error about Python version, upgrade Python to 3.8 or higher:

**On RHEL/CentOS:**
```bash
sudo yum install python3.8
```

**On Ubuntu/Debian:**
```bash
sudo apt-get install python3.8
```

**On macOS:**
```bash
brew install python@3.8
```

### Permission denied errors

If you encounter permission errors during installation, try:

```bash
./install.sh --venv
```

This installs virtbench in a virtual environment without requiring system-level permissions.

## Next Steps

Now that virtbench is installed, you can:

1. **[Review User Guide](reference/user-guide/test-scenarios/overview.md)** - Understand testing scenarios
2. **[Validate Your Cluster](reference/user-guide/test-scenarios/cluster-validation.md)** - Run pre-flight checks
3. **[Run Your First Test](reference/user-guide/test-scenarios/datasource-clone.md)** - Start benchmarking

## Uninstallation

To uninstall virtbench:

```bash
pip3 uninstall virtbench
```

If you used a virtual environment, simply delete the `venv/` directory:

```bash
rm -rf venv/
```
