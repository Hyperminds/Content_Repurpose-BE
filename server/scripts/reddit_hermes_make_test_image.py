"""Dev-only: write a tiny harmless PNG for image-post smoke testing.

Generates a minimal valid PNG (no external deps) at the given path. Not
committed; lives under the gitignored .local dir by default.
"""

import argparse
import base64
import os

# 1x1 (upscaled visually harmless) opaque PNG, base64. Deterministic + tiny.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.path), exist_ok=True)
    with open(args.path, "wb") as f:
        f.write(base64.b64decode(_PNG_B64))
    print(f"wrote {os.path.getsize(args.path)} bytes -> {args.path}")


if __name__ == "__main__":
    main()
