#!/usr/bin/env python3
"""
Test Runner for snflwr.ai
Runs pytest with the correct Python environment and configuration
"""

import sys
import subprocess
from pathlib import Path

def main():
    """Run pytest with appropriate configuration"""

    # Base pytest command
    cmd = [
        sys.executable,  # Use current Python interpreter
        '-m', 'pytest',
        'tests/',  # Test directory
        '--ignore=frontend',  # Ignore frontend tests (third-party)
        '--tb=short',  # Short traceback format
        '-v',  # Verbose output
    ]

    # Add any additional arguments from command line
    cmd.extend(sys.argv[1:])

    print(f"Running: {' '.join(cmd)}\n")

    # Run pytest
    result = subprocess.run(cmd)

    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
