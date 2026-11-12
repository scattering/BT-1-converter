#!/usr/bin/env python3
"""Run bt1modern directly from this checkout, without installing a package."""
from bt1modern.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
