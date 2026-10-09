"""Fail if a lab's own names leak into LabNet's code: they belong in site configuration and docs.

    python .github/scripts/no_site_names.py

Checks every tracked file under packages/, examples/ and tests/ (READMEs are
docs, so they may name a lab as an example). A line that keeps reading an old
2.x name on purpose says so with "(LabNet 2.x)".
"""

from __future__ import annotations

import re
import subprocess
import sys

CHECKED = ("packages/", "examples/", "tests/")
SITE_NAMES = re.compile(r"pqt|192\.168\.50\.", re.IGNORECASE)
ALLOWED = "(LabNet 2.x)"


def main() -> int:
    files = subprocess.run(["git", "ls-files", *CHECKED], capture_output=True, text=True, check=True).stdout.split()
    found = []
    for name in files:
        if name.rsplit("/", 1)[-1].upper().startswith("README"):
            continue
        try:
            text = open(name, encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary or removed
        for number, line in enumerate(text.splitlines(), 1):
            if SITE_NAMES.search(line) and ALLOWED not in line:
                found.append(f"{name}:{number}: {line.strip()[:120]}")
    if found:
        print("A lab's own names in LabNet's code (move them to site configuration or docs):")
        print("\n".join(found))
        return 1
    print(f"No site names in {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
