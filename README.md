<p align="center">
  <img src="docs/virtbench-transparent.png" alt="virtbench logo" width="400"/>
</p>

# KubeVirt Performance Benchmarking Suite

[![Documentation](https://img.shields.io/badge/Read-documentation-blue?logo=readthedocs)][docs-module]
[![Changelog](https://img.shields.io/badge/Changelog-Read-blue)](CHANGELOG.md)

A comprehensive, vendor-neutral performance testing toolkit for KubeVirt virtual machines running on OpenShift Container Platform (OCP) or any Kubernetes distribution with KubeVirt.

## Documentation

For detailed documentation, please refer to the [documentation](https://portworx.github.io/kubevirt-benchmark/) website.

## Overview

This suite provides automated performance testing tools to measure and validate KubeVirt VM provisioning, boot times, network readiness, and failure recovery scenarios. It's designed for production environments running OpenShift Virtualization or KubeVirt with any CSI-compatible storage backend.

> **⚠️ Note:** KubeVirt Benchmark is an independent opensource project and is not affiliated with the Linux Foundation or CNCF.

## Features

- **Unified CLI Interface**: Professional kubectl-like CLI (`virtbench`) with shell completion
- **VM Creation Performance Testing**: Measure VM provisioning and boot times at scale
- **Boot Storm Testing**: Test VM startup performance when powering on multiple VMs simultaneously
- **Live Migration Testing**: Measure VM live migration performance across different scenarios
- **Chaos Benchmark Testing**: Run concurrent chaos operations (VM creation, volume resize, volume clone, VM restart, snapshots) with mandatory concurrency
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



## Maintainers

| Role | GitHub Handle | Responsibilities |
|------|---------------|------------------|
| **Owner** | [@dbhatnagar-px](https://github.com/dbhatnagar-px) | Repository maintenance, CI issues, releases |
| **Owner** | [@nilanto07-px](https://github.com/nilanto07-px) | Repository maintenance, CI issues, releases |
| **Owner** | [@sayalasomayajula-px](https://github.com/sayalasomayajula-px) | Repository maintenance, CI issues, releases |
| **Owner** | [@bnagar-px](https://github.com/bnagar-px) | Repository maintenance, CI issues, releases |
| **Owner** | [@adityadani](https://github.com/adityadani) | Repository maintenance, CI issues, releases |

For questions about this project, please reach out to any of the maintainers above.

---

> **Important Notice:**
>
> - Do not run these benchmarks directly in your production environment without thorough testing first.
> - Always test in a non-production environment to understand the impact and behavior.
> - Test results will vary significantly based on your underlying infrastructure, including hardware specifications, storage backend, network configuration, and cluster resources.
> - **Use at your own risk.**


## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Support

Use the GitHub [issues][gh-issues] to report bugs or suggest features and enhancements. Issues are
monitored and prioritized by the maintainers.


[docs-module]: https://portworx.github.io/kubevirt-benchmark
[gh-issues]: https://github.com/portworx/kubevirt-benchmark/issues
