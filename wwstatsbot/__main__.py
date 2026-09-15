"""Entry point: ``python -m wwstatsbot``.

The wiring and the lifecycle live in main.py, which is where every reference in
CLAUDE.md and in the test suite points. This module exists only so the package is
runnable, which is what the Dockerfile's CMD and the CI smoke test invoke.
"""

from wwstatsbot.main import main

if __name__ == "__main__":
    main()
