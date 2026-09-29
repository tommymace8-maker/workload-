"""Verifiable identity: a document signed by the automaton's own key.

The document binds the wallet address to the automaton's name, its creator,
its parent, its generation, and the digests of its constitution and genesis
prompt. Anyone can check the signature against the address. In EVM mode the
document's hash can also be anchored on-chain (a zero-value self-transaction
carrying the hash as calldata) so the identity has a public timestamp.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import constitution
from .wallet import Keypair, recover_signer


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def create(path: Path, kp: Keypair, *, name: str, creator: str, parent: str | None,
           generation: int, genesis_prompt: str, chain_id: int) -> dict:
    doc = {
        "name": name,
        "address": kp.address,
        "creator": creator,
        "parent": parent,
        "generation": generation,
        "chain_id": chain_id,
        "constitution_sha256": constitution.PINNED_SHA256,
        "genesis_sha256": hashlib.sha256(genesis_prompt.encode()).hexdigest(),
        "created_at": int(time.time()),
    }
    signed = {"document": doc, "signature": kp.sign_message(_canonical(doc))}
    path.write_text(json.dumps(signed, indent=2) + "\n")
    return signed


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def document_hash(signed: dict) -> str:
    return "0x" + hashlib.sha256(_canonical(signed["document"]).encode()).hexdigest()


def verify(signed: dict) -> tuple[bool, str]:
    doc = signed["document"]
    try:
        signer = recover_signer(_canonical(doc), signed["signature"])
    except Exception as exc:  # malformed signature
        return False, f"bad signature: {exc}"
    if signer.lower() != doc["address"].lower():
        return False, f"signed by {signer}, not {doc['address']}"
    if doc["constitution_sha256"] != constitution.PINNED_SHA256:
        return False, "constitution digest differs from this build's pinned constitution"
    return True, "ok"


def anchor(signed: dict, wallet) -> str:
    """Publish the identity hash on-chain from the automaton's own address."""
    return wallet.send_transaction(wallet.address, data=document_hash(signed))
