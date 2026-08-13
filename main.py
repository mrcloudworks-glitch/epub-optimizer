#!/usr/bin/env python3
"""Launch the Kindle EPUB Optimizer desktop application.

Usage:
    python main.py
"""

import sys

from epub_optimizer.app import main

if __name__ == "__main__":
    sys.exit(main())
