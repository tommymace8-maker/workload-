"""Wallets: every automaton owns an Ethereum keypair.

Two balance backends share the same key:

* ``LocalWallet`` keeps a USD-denominated balance in the automaton's ledger.
  Use it for development, or when the creator funds agents off-chain.
* ``EvmWallet`` reads the automaton's real USDC balance on an EVM chain
  (Base by default) via JSON-RPC and signs ERC-20 transfers itself. Compute
  costs accrue in the ledger as unsettled debt and are periodically paid to
  ``compute_payee`` on-chain, so the automaton literally pays for its compute.

The private key lives in ``wallet.json`` in the automaton's home, outside the
sandbox workspace, and is never shown to the model.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct

from .store import Store

USDC = {
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # Base
    84532: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Base Sepolia
}
DEFAULT_RPC = {8453: "https://mainnet.base.org", 84532: "https://sepolia.base.org"}
USDC_DECIMALS = 6


class WalletError(RuntimeError):
    pass


@dataclass
class Keypair:
    address: str
    private_key: str

    @classmethod
    def create(cls, path: Path) -> "Keypair":
        acct = Account.create()
        kp = cls(acct.address, acct.key.hex())
        path.write_text(json.dumps({"address": kp.address, "private_key": kp.private_key}))
        os.chmod(path, 0o600)
        return kp

    @classmethod
    def load(cls, path: Path) -> "Keypair":
        raw = json.loads(path.read_text())
        return cls(raw["address"], raw["private_key"])

    def sign_message(self, text: str) -> str:
        signed = Account.sign_message(encode_defunct(text=text), private_key=self.private_key)
        return "0x" + signed.signature.hex().removeprefix("0x")


def recover_signer(text: str, signature: str) -> str:
    return Account.recover_message(encode_defunct(text=text), signature=signature)


class Wallet:
    """Common interface. Balances are in USD (USDC is treated 1:1)."""

    def __init__(self, keypair: Keypair, store: Store):
        self.keypair = keypair
        self.store = store

    @property
    def address(self) -> str:
        return self.keypair.address

    def balance_usd(self) -> float:
        raise NotImplementedError

    def charge(self, amount_usd: float, memo: str) -> None:
        """Debit compute costs."""
        raise NotImplementedError

    def transfer(self, to: str, amount_usd: float, memo: str) -> str:
        raise NotImplementedError

    def credit(self, amount_usd: float, kind: str, memo: str) -> None:
        """Credit money that arrived off-chain (creator funding, task bounties)."""
        raise NotImplementedError

    def settle(self) -> str | None:
        return None


class LocalWallet(Wallet):
    def balance_usd(self) -> float:
        return self.store.ledger_sum()

    def charge(self, amount_usd: float, memo: str) -> None:
        self.store.record("compute", -amount_usd, memo)

    def credit(self, amount_usd: float, kind: str, memo: str) -> None:
        self.store.record(kind, amount_usd, memo)

    def transfer(self, to: str, amount_usd: float, memo: str) -> str:
        if amount_usd > self.balance_usd():
            raise WalletError("insufficient balance")
        self.store.record("transfer_out", -amount_usd, f"to {to}: {memo}")
        return f"local:{to}"


class EvmWallet(Wallet):
    def __init__(self, keypair: Keypair, store: Store, *, chain_id: int, rpc_url: str | None,
                 usdc_address: str | None, compute_payee: str | None):
        super().__init__(keypair, store)
        self.chain_id = chain_id
        self.rpc_url = rpc_url or DEFAULT_RPC.get(chain_id)
        self.usdc = usdc_address or USDC.get(chain_id)
        self.compute_payee = compute_payee
        if not self.rpc_url or not self.usdc:
            raise WalletError(f"no RPC URL / USDC address known for chain {chain_id}")

    # JSON-RPC ------------------------------------------------------------
    def _rpc(self, method: str, params: list) -> object:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc_url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read())
        if "error" in out:
            raise WalletError(f"{method}: {out['error']}")
        return out["result"]

    def onchain_usdc(self) -> float:
        data = "0x70a08231" + self.address[2:].lower().rjust(64, "0")
        raw = self._rpc("eth_call", [{"to": self.usdc, "data": data}, "latest"])
        return int(raw, 16) / 10**USDC_DECIMALS

    def balance_usd(self) -> float:
        # Unsettled compute debt counts against the balance immediately.
        return self.onchain_usdc() + self.store.ledger_sum(unsettled_only=True)

    def charge(self, amount_usd: float, memo: str) -> None:
        self.store.record("compute", -amount_usd, memo, settled=False)

    def credit(self, amount_usd: float, kind: str, memo: str) -> None:
        # On-chain money shows up in balanceOf; just keep an audit record.
        self.store.log("credit_noted", kind=kind, amount_usd=amount_usd, memo=memo)

    def send_transaction(self, to: str, data: str = "0x", value: int = 0) -> str:
        nonce = int(self._rpc("eth_getTransactionCount", [self.address, "pending"]), 16)
        tip = int(self._rpc("eth_maxPriorityFeePerGas", []), 16)
        base = int(self._rpc("eth_gasPrice", []), 16)
        tx = {"from": self.address, "to": to, "data": data, "value": hex(value)}
        gas = int(self._rpc("eth_estimateGas", [tx]), 16)
        signed = Account.sign_transaction(
            {"type": 2, "chainId": self.chain_id, "nonce": nonce, "to": to, "value": value,
             "data": data, "gas": int(gas * 1.2), "maxPriorityFeePerGas": tip,
             "maxFeePerGas": base * 2 + tip},
            self.keypair.private_key,
        )
        return str(self._rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")]))

    def _erc20_transfer(self, to: str, amount_usd: float) -> str:
        units = int(round(amount_usd * 10**USDC_DECIMALS))
        data = "0xa9059cbb" + to[2:].lower().rjust(64, "0") + hex(units)[2:].rjust(64, "0")
        return self.send_transaction(self.usdc, data)

    def transfer(self, to: str, amount_usd: float, memo: str) -> str:
        if amount_usd > self.balance_usd():
            raise WalletError("insufficient balance")
        tx = self._erc20_transfer(to, amount_usd)
        self.store.record("transfer_out", -amount_usd, f"to {to}: {memo} tx={tx}")
        return tx

    def settle(self) -> str | None:
        """Pay accumulated compute debt to the compute payee."""
        owed = -self.store.ledger_sum(unsettled_only=True)
        if owed < 0.01 or not self.compute_payee:
            return None
        tx = self._erc20_transfer(self.compute_payee, owed)
        self.store.mark_settled()
        self.store.log("settled", amount_usd=owed, tx=tx)
        return tx


def open_wallet(cfg, paths, store: Store) -> Wallet:
    kp = Keypair.load(paths.wallet)
    if cfg.wallet_backend == "evm":
        return EvmWallet(kp, store, chain_id=cfg.chain_id, rpc_url=cfg.rpc_url,
                         usdc_address=cfg.usdc_address, compute_payee=cfg.compute_payee)
    return LocalWallet(kp, store)
