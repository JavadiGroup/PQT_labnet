"""Turn failed tests in a pytest JUnit report into GitHub error annotations.

    python .github/scripts/junit_annotations.py pytest-results.xml

Annotations show on the run's summary page (and through the API) without
opening the log. GitHub shows at most 10 errors per step; the rest are counted.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree

SHOWN = 10


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape(text).replace(":", "%3A").replace(",", "%2C")


def main() -> int:
    failed = []
    for case in ElementTree.parse(sys.argv[1]).iter("testcase"):
        for problem in (*case.findall("failure"), *case.findall("error")):
            name = f"{case.get('classname')}::{case.get('name')}"
            message = problem.get("message") or ""
            detail = "\n".join((problem.text or "").strip().splitlines()[-25:])
            failed.append((name, f"{message}\n{detail}"[:3000]))
    for name, text in failed[:SHOWN]:
        print(f"::error title={escape_property(name)}::{escape(text)}")
    if len(failed) > SHOWN:
        print(f"::error title={len(failed) - SHOWN} more failed tests::See the pytest step's log.")
    print(f"{len(failed)} failed tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
