"""AgentPayClient — a small, explicit facade over the reference client.

Design goals for a third-party integrator:
  - No dependency on this repo's `.env`/global config to *use* the client:
    everything the client needs (key, registry URL, chain id) is passed to the
    constructor. (The underlying modules still import `config`, but the SDK
    never relies on its values — it passes explicit arguments through.)
  - One obvious call for the common case: `purchase(capability_type, resource)`.
  - The lower-level seams (`discover`, `resolve`, `pay_and_fetch`) are exposed
    too, for callers who want to drive discovery themselves.

The heavy lifting (EIP-3009 signing, the 402 handshake, discovery-record
signature verification, the payTo hard-fail) lives in the well-tested
`agent_client` module; this class just binds the per-agent parameters and
delegates, so there is one implementation, not two.
"""

from __future__ import annotations

import agent_client
from agent_client import PurchaseResult, SpendLedger


class AgentPayClient:
    """A buyer-side AgentPay client bound to one agent identity.

    :param private_key: the buyer agent's private key. Used only to *sign*
        EIP-3009 authorizations off-chain — the agent never submits a
        transaction or pays gas itself (the facilitator relays).
    :param registry_url: base URL of a Capability Registry to discover services
        through (in this PoC, the same process that serves the resources).
    :param chain_id: EVM chain id the signatures are bound to (Whitechain
        testnet = 2625). Wrong chain id ⇒ the facilitator rejects the signature.
    :param spend_ledger: optional :class:`SpendLedger` to cap total spend across
        purchases; when set, each purchase checks the limit before signing.
    """

    def __init__(
        self,
        *,
        private_key: str,
        registry_url: str,
        chain_id: int,
        spend_ledger: SpendLedger | None = None,
    ) -> None:
        if not private_key:
            raise ValueError("private_key is required")
        if not registry_url:
            raise ValueError("registry_url is required")
        self._private_key = private_key
        self.registry_url = registry_url.rstrip("/")
        self.chain_id = int(chain_id)
        self.spend_ledger = spend_ledger

    # ---- discovery ----

    def discover(self, capability_type: str | None = None) -> list[dict]:
        """Return raw capability records the registry advertises (optionally
        filtered by ``capability_type``). Records are *not* trusted blindly —
        prefer :meth:`resolve`, which verifies each record's signature."""
        return agent_client.discover_capabilities(self.registry_url, capability_type)

    def resolve(self, capability_type: str, *, prefer_owner: str | None = None) -> dict:
        """Resolve one *signature-verified* provider of ``capability_type`` by a
        named selection criterion (first-registered by default, or an
        allow-listed ``prefer_owner``). Raises
        :class:`~agent_client.CapabilityNotFound` if none verify."""
        return agent_client.resolve_capability(
            self.registry_url, capability_type, prefer_owner=prefer_owner
        )

    # ---- payment ----

    def pay_and_fetch(self, url: str, *, expected_pay_to: str | None = None) -> PurchaseResult:
        """GET ``url``; on 402, sign an EIP-3009 authorization off-chain and
        retry. If ``expected_pay_to`` is given, the 402's ``payTo`` must match it
        or the client refuses to pay (guards against a spoofed ``provider_url``)."""
        return agent_client.pay_and_fetch(
            url,
            private_key=self._private_key,
            ledger=self.spend_ledger,
            chain_id=self.chain_id,
            expected_pay_to=expected_pay_to,
        )

    def purchase(
        self,
        capability_type: str,
        resource_path: str,
        *,
        prefer_owner: str | None = None,
    ) -> PurchaseResult:
        """Discover a verified provider of ``capability_type`` and buy
        ``resource_path`` from it (e.g. ``"/photo/kyiv-lavra"``).

        This is the one-call happy path: it resolves the provider, pins the
        provider's *signed* ``pay_to`` and passes it as ``expected_pay_to`` so a
        compromised ``provider_url`` can't redirect payment, then runs the full
        402 → sign → settle flow. Returns a :class:`PurchaseResult` carrying the
        content, the reputation tier, the fee and the relay tx hash.
        """
        record = self.resolve(capability_type, prefer_owner=prefer_owner)
        base = record["provider_url"].rstrip("/")
        url = f"{base}/{resource_path.lstrip('/')}"
        return self.pay_and_fetch(url, expected_pay_to=record["pay_to"])
