#!/usr/bin/env python3
"""Revision C adapter for the retained, tested USB transaction engine."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('stobs_common', HERE / 'common.py')
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)

if __name__ == '__main__':
    # This preparse performs only regular host-file reads. The engine validates
    # privilege, operator assertion and exact review before device discovery.
    p = argparse.ArgumentParser(add_help=False)
    group = p.add_mutually_exclusive_group(required=True)
    for mode in ('check', 'apply', 'rollback'):
        group.add_argument('--' + mode, type=Path)
    args, _ = p.parse_known_args()
    stage = (args.check or args.apply or args.rollback).resolve()
    c.configure(c.validate_plan(c.load_json(stage / 'plan.json')))
    c.t.main()
