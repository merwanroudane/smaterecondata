#!/usr/bin/env python3
"""Repository-wide brand migration to the SmatEconData identity.

Implements spec section 0.2 (repository migration rule) and section 0Q
(branding cleanup acceptance test).

What this deliberately does NOT touch:

* ``LICENSE`` -- the AGPL-3.0 text and the upstream copyright line
  ``Copyright (C) 2025-2026 OpenEcon (Han Lu Long)`` must be preserved
  verbatim. Section 0.1 requires removing visible *product branding* while
  keeping legally mandatory attribution in the appropriate legal file.
* provider metadata under ``backend/data`` -- those files carry third-party
  copyright notices (IMF, Eurostat, BIS, ...) that are not ours to rewrite.
* generated lockfiles and vendored directories.
* **this file** -- it contains the old brand strings as search patterns, so
  rewriting it would collapse every rule into a no-op identity mapping.

Run with ``--dry-run`` to preview, ``--check`` to fail on residual brand.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SELF = Path(__file__).resolve()
REPO = SELF.parent.parent

# Files whose contents must survive untouched.
# LICENSE and NOTICE carry the upstream copyright that AGPL-3.0 requires us
# to preserve verbatim (spec 0.1); the lockfile is generated.
PROTECTED_FILES = {"LICENSE", "NOTICE", "package-lock.json"}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "mpl_cache",
}

# Provider metadata carries third-party copyright text.
SKIP_PATH_PREFIXES = ("backend/data/metadata", "backend/data/chinamacro")

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yaml", ".yml",
    ".txt", ".html", ".css", ".sh", ".ps1", ".bat", ".conf", ".ini", ".cfg",
    ".toml", ".sql", ".xml", ".svg", ".env", ".example", ".service", ".timer",
    ".webmanifest", ".gitignore", ".gitattributes", ".mailmap",
}

# The old brand, assembled at runtime so this file never contains a literal
# that a future run of itself could match.
_OE = "open" + "econ"
_OWNER = "hanlu" + "long"

NEW_OWNER = "merwanroudane"
NEW_REPO = "smaterecondata"
NEW_PRODUCT = "SmatEconData"
NEW_SLUG = "smatecondata"
NEW_EMAIL = "merwanroudane920@gmail.com"
NEW_REPO_URL = f"https://github.com/{NEW_OWNER}/{NEW_REPO}"

# Ordered: most specific first. Order is load-bearing.
REPLACEMENTS: list[tuple[str, str]] = [
    # --- repository URLs -------------------------------------------------
    (f"https://raw.githubusercontent.com/{_OWNER}/{_OE}-data",
     f"https://raw.githubusercontent.com/{NEW_OWNER}/{NEW_REPO}"),
    (f"https://github.com/{_OWNER}/{_OE}-data.git", f"{NEW_REPO_URL}.git"),
    (f"https://github.com/{_OWNER}/{_OE}-data", NEW_REPO_URL),
    (f"github.com/{_OWNER}/{_OE}-data", f"github.com/{NEW_OWNER}/{NEW_REPO}"),
    (f"{_OWNER}/{_OE}-data", f"{NEW_OWNER}/{NEW_REPO}"),

    # --- hosted service -> self-hosted default ---------------------------
    # There is no hosted SmatEconData service; point at the local backend so
    # instructions stay executable instead of naming a domain that does not
    # resolve.
    (f"https://data.{_OE}.ai", "http://localhost:3001"),
    (f"https://www.{_OE}.ai", NEW_REPO_URL),
    (f"https://{_OE}.ai", NEW_REPO_URL),
    (f"data.{_OE}.ai", "localhost:3001"),
    (f"data.{_OE}.io", "localhost:3001"),
    (f"www.{_OE}.ai", f"github.com/{NEW_OWNER}/{NEW_REPO}"),
    (f"contact@{_OE}.ai", NEW_EMAIL),
    (f"security@{_OE}.ai", NEW_EMAIL),
    (f"deploy@{_OE}.ai", NEW_EMAIL),
    (f"{_OE}.ai", f"github.com/{NEW_OWNER}/{NEW_REPO}"),

    # --- deployment paths / services -------------------------------------
    (f"/home/{_OWNER}/{_OE}-data", f"/opt/{NEW_SLUG}"),
    (f"/home/{_OWNER}/OpenEcon", f"/opt/{NEW_SLUG}"),
    (f"/var/log/{_OE}", f"/var/log/{NEW_SLUG}"),
    (f"{_OE}-backend.service", f"{NEW_SLUG}-backend.service"),
    (f"{_OE}-mcp.service", f"{NEW_SLUG}-mcp.service"),
    (f"{_OE}-website-monitor", f"{NEW_SLUG}-website-monitor"),
    (f"{_OE}-data-local", f"{NEW_SLUG}-local"),

    # --- identifiers ------------------------------------------------------
    (f"{_OE.upper()}_", f"{NEW_SLUG.upper()}_"),
    (f"{_OE}_", f"{NEW_SLUG}_"),
    (f"{_OE}-data", NEW_REPO),
    (_OWNER, NEW_OWNER),

    # --- product name -----------------------------------------------------
    ("OpenEcon Data", NEW_PRODUCT),
    ("OpenEcon.ai", NEW_PRODUCT),
    ("OpenEcon", NEW_PRODUCT),
    (_OE.upper(), NEW_SLUG.upper()),
    (_OE, NEW_SLUG),
]

RESIDUAL = re.compile(f"{_OE}|{_OWNER}", re.IGNORECASE)


def is_candidate(path: Path) -> bool:
    if path.resolve() == SELF:
        return False
    rel = path.relative_to(REPO).as_posix()
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name in PROTECTED_FILES:
        return False
    if rel.startswith(SKIP_PATH_PREFIXES):
        return False
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.suffix == "" and path.name.startswith(".")


def iter_files():
    for path in sorted(REPO.rglob("*")):
        if path.is_file() and is_candidate(path):
            yield path


def migrate(dry_run: bool) -> int:
    changed: list[tuple[str, int]] = []
    for path in iter_files():
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        updated = original
        for old, new in REPLACEMENTS:
            updated = updated.replace(old, new)

        if updated != original:
            hits = len(RESIDUAL.findall(original))
            changed.append((path.relative_to(REPO).as_posix(), hits))
            if not dry_run:
                path.write_text(updated, encoding="utf-8", newline="\n")

    verb = "would rewrite" if dry_run else "rewrote"
    print(f"{verb} {len(changed)} files")
    for rel, hits in changed[:40]:
        print(f"  {rel}  ({hits} brand tokens)")
    if len(changed) > 40:
        print(f"  ... and {len(changed) - 40} more")
    return len(changed)


def check() -> int:
    """Spec 0Q acceptance test: no old brand outside protected legal files."""
    offenders: list[tuple[str, int]] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = len(RESIDUAL.findall(text))
        if hits:
            offenders.append((path.relative_to(REPO).as_posix(), hits))

    named = [
        p.relative_to(REPO).as_posix()
        for p in REPO.rglob("*")
        if RESIDUAL.search(p.name)
        and not any(part in SKIP_DIRS for part in p.parts)
    ]

    if not offenders and not named:
        print("PASS: no residual brand strings in non-protected files.")
        return 0

    for rel, hits in offenders:
        print(f"FAIL {rel}: {hits} residual brand tokens")
    for rel in named:
        print(f"FAIL filename still carries old brand: {rel}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="fail if any residual brand string remains")
    args = parser.parse_args()
    if args.check:
        return check()
    migrate(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
