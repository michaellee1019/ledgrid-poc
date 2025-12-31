#!/usr/bin/env python3
"""
Utility to extract and decode the compressed frame payload from status.json.

Useful for debugging when the controller writes compressed frame data and a
human-readable dump is needed.
"""

import argparse
import json
from pathlib import Path

from drivers.frame_codec import decode_frame_data


def load_status_payload(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No status file found at {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser(description="Decode frame_data from a status JSON file")
    parser.add_argument(
        "--status-file",
        default="run_state/status.json",
        help="Path to the status.json file (default: run_state/status.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Optional path to write the decoded frame JSON; prints to stdout if omitted",
    )
    args = parser.parse_args()

    status_path = Path(args.status_file)
    payload = load_status_payload(status_path)

    encoded = payload.get("frame_data_encoded")
    raw_frame = payload.get("frame_data")
    if isinstance(raw_frame, list):
        decoded = raw_frame
    else:
        encoded = encoded or (raw_frame if isinstance(raw_frame, str) else "")
        decoded = decode_frame_data(encoded)

    print(f"Decoded {len(decoded)} pixels from {status_path}")

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(decoded), encoding="utf-8")
        print(f"Decoded frame written to {output_path}")
    else:
        print(json.dumps(decoded, indent=2))


if __name__ == "__main__":
    main()
