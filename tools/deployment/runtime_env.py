#!/usr/bin/env python3
"""Import the runtime entrypoints without opening hardware or starting Flask."""
import argparse
from pathlib import Path
import sys


def smoke_runtime(root: Path) -> None:
    sys.path.insert(0, str(root.resolve()))
    import scripts.start_server  # noqa: F401
    from web.app import create_app  # noqa: F401


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["smoke"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    smoke_runtime(parser.parse_args().root)
