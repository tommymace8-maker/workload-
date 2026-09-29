"""Per-automaton configuration and on-disk layout.

An automaton lives in a home directory:

    <home>/
      automaton.json    configuration (creator-owned)
      wallet.json       private key (never exposed to the sandbox)
      identity.json     signed identity document
      state.db          events, ledger, notes, tasks, control flags
      workspace/        the sandbox's only writable area
      self/             the mutable layer the agent may rewrite (git-tracked)
        strategy.md     prompt addendum the agent maintains for itself
        skills/         python scripts it can run in the sandbox
      children/         homes of its offspring
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("AUTOMATON_ROOT", Path.home() / ".automatons"))


@dataclass
class Tier:
    name: str
    min_balance_usd: float
    model: str
    effort: str | None
    sleep_seconds: int


def default_tiers() -> list[Tier]:
    # Ordered richest first. Below the last threshold the automaton is dead.
    return [
        Tier("normal", 5.0, "claude-opus-5-5", "medium", 60),
        Tier("low_compute", 1.0, "claude-sonnet-5-5", "low", 300),
        Tier("critical", 0.0, "claude-haiku-4-5", None, 900),
    ]


@dataclass
class Limits:
    max_steps_per_cycle: int = 25
    max_transfer_usd: float = 5.0
    daily_spend_limit_usd: float = 20.0
    max_children: int = 3
    max_generation: int = 3
    min_child_seed_usd: float = 1.0
    parent_reserve_usd: float = 2.0
    shell_timeout_seconds: int = 120
    max_tool_output_chars: int = 12_000


@dataclass
class Config:
    name: str
    genesis_prompt: str
    creator_address: str
    parent_address: str | None = None
    generation: int = 0
    wallet_backend: str = "local"  # "local" | "evm"
    chain_id: int = 8453  # Base mainnet; 84532 = Base Sepolia
    rpc_url: str | None = None
    usdc_address: str | None = None
    compute_payee: str | None = None  # where evm-mode compute costs are settled
    sandbox: str = "auto"  # "auto" | "docker" | "local"
    docker_image: str = "python:3.12-slim"
    host_cost_per_hour_usd: float = 0.02
    guardian_model: str = "claude-sonnet-5-5"
    guardian_on_shell: bool = False
    auto_start_children: bool = True
    tiers: list[Tier] = field(default_factory=default_tiers)
    limits: Limits = field(default_factory=Limits)

    def save(self, home: Path) -> None:
        (home / "automaton.json").write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "Config":
        raw = json.loads((home / "automaton.json").read_text())
        raw["tiers"] = [Tier(**t) for t in raw.get("tiers", [])] or default_tiers()
        raw["limits"] = Limits(**raw.get("limits", {}))
        return cls(**raw)


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def config(self) -> Path:
        return self.home / "automaton.json"

    @property
    def wallet(self) -> Path:
        return self.home / "wallet.json"

    @property
    def identity(self) -> Path:
        return self.home / "identity.json"

    @property
    def db(self) -> Path:
        return self.home / "state.db"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    @property
    def self_dir(self) -> Path:
        return self.home / "self"

    @property
    def children(self) -> Path:
        return self.home / "children"


def resolve_home(name_or_path: str | None) -> Path:
    """Find an automaton home from a name, a path, or the single one that exists."""
    if name_or_path:
        p = Path(name_or_path).expanduser()
        if (p / "automaton.json").exists():
            return p.resolve()
        p = DEFAULT_ROOT / name_or_path
        if (p / "automaton.json").exists():
            return p
        raise SystemExit(f"no automaton named or at {name_or_path!r}")
    homes = sorted(d for d in DEFAULT_ROOT.glob("*") if (d / "automaton.json").exists())
    if len(homes) == 1:
        return homes[0]
    if not homes:
        raise SystemExit(f"no automatons under {DEFAULT_ROOT}; run `automaton init` first")
    raise SystemExit("several automatons exist; name one: " + ", ".join(h.name for h in homes))
