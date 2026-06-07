#!/usr/bin/env python3
"""
Cluster Validation Script for KubeVirt Benchmark Suite

This script validates that the cluster is ready for running KubeVirt benchmarks.
It checks for required components, resources, and configurations.

Usage:
    python3 validate_cluster.py --storage-class YOUR-STORAGE-CLASS
    python3 validate_cluster.py --all
"""

import argparse
import sys
import subprocess
import json
from typing import Tuple, Optional, Dict, List
import logging

# Add parent directory to path for imports
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.common import setup_logging, run_kubectl_command


class ClusterValidator:
    """Validates cluster readiness for KubeVirt benchmarks"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings = 0
        self.results = []
    
    def run_check(self, check_name: str, check_func, *args, **kwargs) -> bool:
        """Run a validation check and track results"""
        self.logger.info(f"Checking: {check_name}...")
        try:
            result, message = check_func(*args, **kwargs)
            if result:
                self.logger.info(f"  ✓ PASS: {message}")
                self.checks_passed += 1
                self.results.append({"check": check_name, "status": "PASS", "message": message})
                return True
            else:
                self.logger.error(f"  ✗ FAIL: {message}")
                self.checks_failed += 1
                self.results.append({"check": check_name, "status": "FAIL", "message": message})
                return False
        except Exception as e:
            self.logger.error(f"  ✗ ERROR: {str(e)}")
            self.checks_failed += 1
            self.results.append({"check": check_name, "status": "ERROR", "message": str(e)})
            return False
    
    def check_kubectl_access(self) -> Tuple[bool, str]:
        """Verify kubectl is installed and can access the cluster"""
        returncode, stdout, stderr = run_kubectl_command(['cluster-info'], check=False, logger=self.logger)
        if returncode == 0:
            return True, "kubectl is installed and cluster is accessible"
        return False, f"Cannot access cluster: {stderr}"
    
    def check_kubevirt_installed(self) -> Tuple[bool, str]:
        """Verify KubeVirt/OpenShift Virtualization is installed"""
        # First, check for KubeVirt resource (OpenShift Virtualization)
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'kubevirt', '-A', '-o', 'json'],
            check=False,
            logger=self.logger
        )

        if returncode == 0:
            data = json.loads(stdout)
            items = data.get('items', [])
            if len(items) > 0:
                kubevirt = items[0]
                namespace = kubevirt.get('metadata', {}).get('namespace', 'unknown')
                name = kubevirt.get('metadata', {}).get('name', 'unknown')
                phase = kubevirt.get('status', {}).get('phase', 'Unknown')

                if phase == 'Deployed':
                    # Now check critical deployments in openshift-cnv namespace
                    return self._check_kubevirt_components(namespace)
                else:
                    return False, f"KubeVirt '{name}' found in namespace '{namespace}' but phase is '{phase}' (expected: Deployed)"
            else:
                return False, "No KubeVirt resource found. Is OpenShift Virtualization installed?"
        else:
            return False, "Cannot check KubeVirt resource. Is OpenShift Virtualization installed?"

    def _check_kubevirt_components(self, namespace: str) -> Tuple[bool, str]:
        """Check critical KubeVirt components are running"""
        # Check critical deployments
        critical_deployments = ['virt-api', 'virt-controller', 'virt-operator']

        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'deployment', '-n', namespace, '-o', 'json'],
            check=False,
            logger=self.logger
        )

        if returncode != 0:
            return False, f"Cannot check deployments in namespace '{namespace}'"

        data = json.loads(stdout)
        deployments = data.get('items', [])

        found_deployments = {}
        for dep in deployments:
            dep_name = dep.get('metadata', {}).get('name', '')
            if any(critical in dep_name for critical in critical_deployments):
                status = dep.get('status', {})
                ready = status.get('readyReplicas', 0)
                desired = status.get('replicas', 0)
                found_deployments[dep_name] = (ready, desired)

        # Check if all critical deployments are ready
        not_ready = []
        for dep_name in critical_deployments:
            matching = [k for k in found_deployments.keys() if dep_name in k]
            if not matching:
                not_ready.append(f"{dep_name} (not found)")
            else:
                for match in matching:
                    ready, desired = found_deployments[match]
                    if ready != desired or ready == 0:
                        not_ready.append(f"{match} ({ready}/{desired})")

        if not_ready:
            return False, f"KubeVirt components not ready: {', '.join(not_ready)}"

        # Check virt-handler daemonset
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'daemonset', 'virt-handler', '-n', namespace, '-o', 'json'],
            check=False,
            logger=self.logger
        )

        if returncode == 0:
            data = json.loads(stdout)
            status = data.get('status', {})
            desired = status.get('desiredNumberScheduled', 0)
            ready = status.get('numberReady', 0)

            if ready != desired or ready == 0:
                return False, f"virt-handler daemonset not ready ({ready}/{desired} pods ready)"

            return True, f"OpenShift Virtualization is deployed in '{namespace}' (virt-api, virt-controller, virt-operator, virt-handler ready)"
        else:
            self.warnings += 1
            return True, f"OpenShift Virtualization is deployed in '{namespace}' (virt-handler check skipped) - WARNING"
    
    def check_storage_class(self, storage_class_name: str) -> Tuple[bool, str]:
        """Verify storage class exists and is available"""
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'storageclass', storage_class_name, '-o', 'json'],
            check=False,
            logger=self.logger
        )
        if returncode != 0:
            return False, f"Storage class '{storage_class_name}' not found"
        
        data = json.loads(stdout)
        provisioner = data.get('provisioner', 'unknown')
        return True, f"Storage class '{storage_class_name}' exists (provisioner: {provisioner})"
    
    def check_worker_nodes(self, min_nodes: int = 1) -> Tuple[bool, str]:
        """Verify sufficient worker nodes are available"""
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'nodes', '-l', 'node-role.kubernetes.io/worker', '-o', 'json'],
            check=False,
            logger=self.logger
        )
        if returncode != 0:
            return False, "Cannot get worker nodes"
        
        data = json.loads(stdout)
        nodes = data.get('items', [])
        ready_nodes = []
        
        for node in nodes:
            conditions = node.get('status', {}).get('conditions', [])
            for condition in conditions:
                if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                    ready_nodes.append(node.get('metadata', {}).get('name'))
                    break
        
        if len(ready_nodes) >= min_nodes:
            return True, f"{len(ready_nodes)} worker nodes ready: {', '.join(ready_nodes[:3])}"
        return False, f"Only {len(ready_nodes)} worker nodes ready (minimum: {min_nodes})"
    
    def check_node_resources(self) -> Tuple[bool, str]:
        """Check if nodes have sufficient resources"""
        returncode, stdout, stderr = run_kubectl_command(
            ['top', 'nodes'],
            check=False,
            logger=self.logger
        )
        if returncode != 0:
            self.warnings += 1
            return True, "Cannot check node resources (metrics-server may not be installed) - WARNING"
        
        lines = stdout.strip().split('\n')[1:]  # Skip header
        if len(lines) == 0:
            return True, "No resource metrics available - WARNING"
        
        overloaded_nodes = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                node_name = parts[0]
                cpu_usage = parts[1]
                if cpu_usage.endswith('%'):
                    cpu_pct = int(cpu_usage.rstrip('%'))
                    if cpu_pct > 80:
                        overloaded_nodes.append(f"{node_name} ({cpu_pct}% CPU)")
        
        if overloaded_nodes:
            self.warnings += 1
            return True, f"Some nodes are heavily loaded: {', '.join(overloaded_nodes)} - WARNING"
        
        return True, f"Node resources look healthy ({len(lines)} nodes checked)"
    
    def check_datasource(self, datasource_name: str, namespace: str) -> Tuple[bool, str]:
        """Verify DataSource exists"""
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'datasource', datasource_name, '-n', namespace, '-o', 'json'],
            check=False,
            logger=self.logger
        )
        if returncode != 0:
            return False, f"DataSource '{datasource_name}' not found in namespace '{namespace}'"
        
        data = json.loads(stdout)
        conditions = data.get('status', {}).get('conditions', [])
        ready = any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions)
        
        if ready:
            return True, f"DataSource '{datasource_name}' is ready in namespace '{namespace}'"
        return False, f"DataSource '{datasource_name}' exists but is not ready"
    
    def check_ssh_pod(self, pod_name: str, namespace: str) -> Tuple[bool, str]:
        """Verify SSH test pod exists and is running"""
        returncode, stdout, stderr = run_kubectl_command(
            ['get', 'pod', pod_name, '-n', namespace, '-o', 'json'],
            check=False,
            logger=self.logger
        )
        if returncode != 0:
            self.warnings += 1
            return True, f"SSH test pod '{pod_name}' not found in namespace '{namespace}' - WARNING (optional)"
        
        data = json.loads(stdout)
        phase = data.get('status', {}).get('phase')
        if phase == 'Running':
            return True, f"SSH test pod '{pod_name}' is running in namespace '{namespace}'"
        
        self.warnings += 1
        return True, f"SSH test pod '{pod_name}' exists but is not running (phase: {phase}) - WARNING"
    
    def check_permissions(self) -> Tuple[bool, str]:
        """Verify user has required permissions"""
        permissions = [
            ('create', 'namespace'),
            ('create', 'virtualmachine'),
            ('create', 'virtualmachineinstance'),
            ('delete', 'namespace'),
        ]
        
        missing_perms = []
        for verb, resource in permissions:
            returncode, stdout, stderr = run_kubectl_command(
                ['auth', 'can-i', verb, resource, '--all-namespaces'],
                check=False,
                logger=self.logger
            )
            if returncode != 0 or stdout.strip().lower() != 'yes':
                missing_perms.append(f"{verb} {resource}")
        
        if missing_perms:
            return False, f"Missing permissions: {', '.join(missing_perms)}"
        return True, "User has all required permissions"
    
    def print_summary(self):
        """Print validation summary"""
        self.logger.info("\n" + "=" * 80)
        self.logger.info("VALIDATION SUMMARY")
        self.logger.info("=" * 80)
        self.logger.info(f"Checks Passed:  {self.checks_passed}")
        self.logger.info(f"Checks Failed:  {self.checks_failed}")
        self.logger.info(f"Warnings:       {self.warnings}")
        self.logger.info("=" * 80)
        
        if self.checks_failed == 0:
            self.logger.info("✓ Cluster is ready for KubeVirt benchmarks!")
            return True
        else:
            self.logger.error("✗ Cluster validation failed. Please fix the issues above.")
            return False


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Validate cluster readiness for KubeVirt benchmarks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate with specific storage class
  %(prog)s --storage-class YOUR-STORAGE-CLASS

  # Validate all components
  %(prog)s --all

  # Validate with custom DataSource
  %(prog)s --storage-class YOUR-STORAGE-CLASS --datasource rhel9 --datasource-namespace openshift-virtualization-os-images
        """
    )
    
    parser.add_argument(
        '--storage-class',
        type=str,
        help='Storage class name to validate'
    )
    parser.add_argument(
        '--datasource',
        type=str,
        default='rhel9',
        help='DataSource name to validate (default: rhel9)'
    )
    parser.add_argument(
        '--datasource-namespace',
        type=str,
        default='openshift-virtualization-os-images',
        help='DataSource namespace (default: openshift-virtualization-os-images)'
    )
    parser.add_argument(
        '--ssh-pod',
        type=str,
        default='ssh-test-pod',
        help='SSH test pod name (default: ssh-test-pod)'
    )
    parser.add_argument(
        '--ssh-pod-namespace',
        type=str,
        default='default',
        help='SSH test pod namespace (default: default)'
    )
    parser.add_argument(
        '--min-worker-nodes',
        type=int,
        default=1,
        help='Minimum number of worker nodes required (default: 1)'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Run all validation checks'
    )
    parser.add_argument(
        '--quick',
        action='store_true',
        help='Run only core connectivity, virtualization, permission, worker-node, and storage-class checks'
    )
    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level (default: INFO)'
    )
    parser.add_argument(
        '--kubeconfig',
        type=str,
        help='Path to kubeconfig file'
    )
    
    return parser.parse_args()


def main():
    """Main execution function"""
    args = parse_args()

    if args.kubeconfig:
        os.environ['KUBECONFIG'] = args.kubeconfig
    
    # Setup logging
    logger = setup_logging(log_file=None, log_level=args.log_level)
    
    logger.info("=" * 80)
    logger.info("KubeVirt Benchmark Suite - Cluster Validation")
    logger.info("=" * 80)
    
    validator = ClusterValidator(logger)
    
    # Core checks (always run)
    if not validator.run_check("kubectl access", validator.check_kubectl_access):
        validator.print_summary()
        sys.exit(1)

    validator.run_check("OpenShift Virtualization installation", validator.check_kubevirt_installed)
    validator.run_check("User permissions", validator.check_permissions)
    validator.run_check("Worker nodes", validator.check_worker_nodes, args.min_worker_nodes)
    
    # Storage class check
    if args.storage_class:
        validator.run_check(f"Storage class '{args.storage_class}'", validator.check_storage_class, args.storage_class)
    elif args.all:
        logger.warning("No storage class specified. Use --storage-class to validate.")
    
    # Optional checks
    if not args.quick and (args.all or args.datasource):
        validator.run_check(
            f"DataSource '{args.datasource}'",
            validator.check_datasource,
            args.datasource,
            args.datasource_namespace
        )
    
    if not args.quick and (args.all or args.ssh_pod):
        validator.run_check(
            f"SSH test pod '{args.ssh_pod}'",
            validator.check_ssh_pod,
            args.ssh_pod,
            args.ssh_pod_namespace
        )
    
    # Resource checks
    if not args.quick:
        validator.run_check("Node resources", validator.check_node_resources)
    
    # Print summary
    success = validator.print_summary()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
