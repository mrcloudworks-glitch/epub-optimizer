"""Allow launching with ``python -m epub_optimizer``."""

from epub_optimizer.app import main

if __name__ == "__main__":
    raise SystemExit(main())
