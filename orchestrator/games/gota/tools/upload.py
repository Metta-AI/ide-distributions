#!/usr/bin/env python3
"""Delegate upload to the shared CLI's per-mutation identity gate."""
import json
import os
import subprocess
import sys


def main():
    executable = os.environ.get('RESEARCH_CLI')
    if not executable:
        print(json.dumps({'ok': False, 'error': 'set RESEARCH_CLI to the installed shared CLI'}))
        return 2
    return subprocess.run([executable, 'softmax', 'upload', *sys.argv[1:]], check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
