#!/usr/bin/env python3
"""Ponto de entrada: python3 main.py."""
from filesystem.cli import main

if __name__ == "__main__":
    import sys

    if {"--security", "--memory"}.intersection(sys.argv[1:]):
        from filesystem.disk_io import main
    raise SystemExit(main())
