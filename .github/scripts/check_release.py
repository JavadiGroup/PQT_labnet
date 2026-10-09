"""Check a release before anything is published.

    python .github/scripts/check_release.py dist [v3.0.0]

- every package in packages/ has the same version, and pins its LabNet
  siblings from that version on;
- the tag, if given, is "v" + that version;
- dist/ holds a wheel and an sdist of each package at that version, and
  nothing else;
- CHANGELOG.md has a dated section for it (not "unreleased").

Prints the changelog section, for the release notes, to dist-notes.md.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    dist = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) > 2 else None
    problems = []

    projects = {}
    for pyproject in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        projects[data["name"]] = data
    versions = {project["version"] for project in projects.values()}
    if len(versions) != 1:
        problems.append(f"the packages have different versions: {sorted(versions)}")
    version = sorted(versions)[0]
    for name, project in projects.items():
        for requirement in project.get("dependencies", []):
            match = re.match(r"(labnet[a-z-]*)\s*>=\s*([^,]+)", requirement)
            if match and match.group(1) in projects and match.group(2) != version:
                problems.append(f"{name} requires {requirement}; it should start at {version}")

    if tag is not None and tag != f"v{version}":
        problems.append(f"the tag is {tag} but the packages are {version}")

    expected = set()
    for name in projects:
        stem = re.sub(r"[-_.]+", "_", name).lower()
        expected |= {f"{stem}-{version}-py3-none-any.whl", f"{stem}-{version}.tar.gz"}
    found = {path.name for path in dist.iterdir() if path.is_file() and path.name != ".gitignore"}
    if found != expected:
        problems.append(f"dist/ is not one wheel and one sdist per package: missing {sorted(expected - found)}, "
                        f"unexpected {sorted(found - expected)}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(rf"^## \[{re.escape(version)}\] - (.+?)$(.*?)(?=^## \[|\Z)", changelog, re.M | re.S)
    if section is None:
        problems.append(f"CHANGELOG.md has no section for {version}")
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", section.group(1).strip()):
        problems.append(f"CHANGELOG.md's {version} section is dated {section.group(1).strip()!r}, not YYYY-MM-DD")
    else:
        (dist.parent / "dist-notes.md").write_text(section.group(2).strip() + "\n", encoding="utf-8")

    if problems:
        print("Not releasable:\n- " + "\n- ".join(problems))
        return 1
    print(f"LabNet {version}: {len(projects)} packages, {len(found)} files, changelog section dated "
          f"{section.group(1).strip()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
