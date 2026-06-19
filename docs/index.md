<h1 style="display: flex; align-items: center; gap: 10px;">
  <img src="virtbench-transparent.png" alt="virtbench logo" style="height: 5em; width: auto;"/>
  <span>KubeVirt Performance Benchmarking Suite</span>
</h1>

A comprehensive, vendor-neutral performance testing toolkit for KubeVirt virtual machines running on OpenShift Container Platform (OCP) or any Kubernetes distribution with KubeVirt.

## Overview

This suite provides automated performance testing tools to measure and validate KubeVirt VM provisioning, boot times, network readiness, and failure recovery scenarios. It's designed for production environments running OpenShift Virtualization or KubeVirt with any CSI-compatible storage backend.

## Key Features

- **Unified CLI Interface**: Professional kubectl-like CLI (`virtbench`) with shell completion
- **VM Creation Performance Testing**: Measure VM provisioning and boot times at scale
- **Boot Storm Testing**: Test VM startup performance when powering on multiple VMs simultaneously
- **Live Migration Testing**: Measure VM live migration performance across different scenarios
- **Chaos Benchmark Testing**: Stress-test the cluster with concurrent chaos operations (VM creation, volume resize, volume clone, VM restart, snapshots)
- **Single Node Testing**: Pin all VMs to a single node for node-level capacity testing
- **Failure and Recovery Testing**: Validate VM recovery times after node failures
- **VM Snapshot Testing**: Test VM snapshot creation and readiness
- **Volume Resize Testing**: Test PVC expansion capabilities
- **Parallel Execution**: Support for testing hundreds of VMs concurrently
- **Parallel Namespace Creation**: Create namespaces in batches for faster test setup
- **Multiple Storage Backends**: Works with any CSI-compatible storage class (Portworx, Ceph, vSphere, AWS EBS, etc.)
- **Comprehensive Logging**: Detailed logs with timestamps and error tracking
- **Flexible Configuration**: Command-line arguments for easy customization
- **Interactive Results Dashboard**: Auto-generate rich HTML dashboards for all test results

## Quick Start

Get started in minutes:

1. **[Install virtbench](install.md)** - Set up the virtbench CLI
2. **[User Guide](reference/user-guide/test-scenarios/overview.md)** - Overview of testing scenarios

> **Important Notice:**
>
> - Do not run these benchmarks directly in your production environment without thorough testing first.
> - Always test in a non-production environment to understand the impact and behavior.
> - Test results will vary significantly based on your underlying infrastructure, including hardware specifications, storage backend, network configuration, and cluster resources.
> - **Use at your own risk.**


## License

This project is licensed under the Apache License 2.0. See [LICENSE](license.md) for details.
