from __future__ import annotations

import argparse
import json
from pathlib import Path

from world_engine_startup import automatic_startup


def main() -> int:
    parser = argparse.ArgumentParser(description="World Engine 5.1.0 automatic permanent endpoint setup")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--clipboard-timeout", type=int, default=600)
    parser.add_argument("--provider", choices=("ngrok",), default="ngrok",
                        help="No-admin stable endpoint provider; currently ngrok user mode.")
    args = parser.parse_args()
    result = automatic_startup(
        args.root, interactive=True, allow_download=True,
        clipboard_timeout=args.clipboard_timeout, launch_ui=False,
        reveal_setup_artifacts=True, force_copy_api_key=True,
    )
    print(json.dumps({
        "status": result["status"],
        "public_url": result["endpoint"]["public_url"],
        "schema": result["endpoint"]["schema"],
        "api_key_fingerprint": result["api_key_fingerprint"],
        "api_key_copied_to_clipboard": result["api_key_copied_to_clipboard"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
