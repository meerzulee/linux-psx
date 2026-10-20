#!/usr/bin/env python3
"""Offline source/lineage checks; no GPU or build-tree access."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
record = json.loads((ROOT / 'provenance/mesa-import.json').read_text())
for item in record['files']:
    path = (ROOT / item['destination']).resolve(strict=True)
    assert path.is_relative_to(HERE)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['destination_sha256'], path
pins = json.loads((HERE / 'sources.json').read_text())
for item in pins['patches']:
    path = HERE / item['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], path
assert pins['upstream']['version'] == '26.2.5'
assert len(pins['patches']) == 3
assert pins['hardware_acceptance'] is False
subprocess.run([sys.executable, '-B', str(HERE / 'checks/demo_check.py')], check=True)
print(f'PASS: {len(record["files"])} migrated source identities, three pinned Mesa patches, portable player checks; no hardware access')
