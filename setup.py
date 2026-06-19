#!/usr/bin/env python3
"""
Setup configuration for virtbench CLI
"""

from setuptools import find_packages, setup


setup(
    name="virtbench",
    version="2.0.0",
    description="KubeVirt Benchmark Suite - Performance testing toolkit for KubeVirt VMs",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "click>=8.1.7",
        "rich>=13.7.0",
        "pyyaml>=6.0.3",
        "pandas>=2.3.3",
    ],
    entry_points={
        "console_scripts": [
            "virtbench=virtbench.cli:main",
        ],
    },
)
