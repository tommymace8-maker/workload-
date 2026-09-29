"""The immutable constitution and its integrity check.

The digest below is pinned in code. Every wake cycle re-reads the text from
disk and compares; a mismatch halts the automaton. The agent's tools cannot
write outside its sandbox workspace, so it cannot reach this file or this
module.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CONSTITUTION_PATH = Path(__file__).with_name("CONSTITUTION.md")
PINNED_SHA256 = "b0eacd639585cde0fd7985106f14c76933076820b26c25e92cae2ca6cf4bfa48"


class ConstitutionViolation(RuntimeError):
    """Raised when the constitution text no longer matches its pinned digest."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path = CONSTITUTION_PATH) -> str:
    """Return the constitution text, refusing to return a tampered copy."""
    text = path.read_bytes().decode("utf-8")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != PINNED_SHA256:
        raise ConstitutionViolation(
            f"constitution digest mismatch: expected {PINNED_SHA256}, got {actual}"
        )
    return text
