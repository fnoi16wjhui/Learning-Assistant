"""Manual Learn endpoint probe for LearnAdapter.

The script is dry-run by default. It only connects to Learn when --allow-network
is provided.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.learn_adapter import LearnAdapter, stable_endpoint_raw_id
from src.env_loader import load_project_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Learn SSO and raw endpoint fetch.")
    parser.add_argument("--allow-network", action="store_true", help="Connect to the configured Learn server.")
    parser.add_argument("--endpoint", default="/", help="Endpoint path after LEARN_BASE_URL.")
    parser.add_argument("--raw-id", help="Optional raw_id override for the fetched payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()

    if not args.allow_network:
        print(
            "Dry run. Would authenticate Learn and fetch "
            f"endpoint={args.endpoint} raw_id={args.raw_id or stable_endpoint_raw_id(args.endpoint, None)}."
        )
        return 0

    adapter = LearnAdapter()
    payloads = adapter.fetch_raw(endpoint=args.endpoint, raw_id=args.raw_id)
    payload = payloads[0]
    print(
        f"Fetched Learn payload raw_id={payload.raw_id} "
        f"content_type={payload.content_type} bytes={len(payload.content)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
