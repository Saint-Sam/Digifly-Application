"""Read and validate the shared Mac and Windows release number."""
import argparse
from pathlib import Path
import re


def validate(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d{0,2})", value):
        raise ValueError("Use a short three-part version, such as 0.2.0")
    return value


def read(source: Path, override: str = "") -> str:
    return validate(override or (source / "VERSION").read_text("utf-8").strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--value", default="")
    args = parser.parse_args()
    print(read(args.source, args.value))
