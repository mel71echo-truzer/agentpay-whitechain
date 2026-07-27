"""x402-facilitator — ТОНКИЙ оркестратор (Фаза 2, Компонент 1).

Раніше (Фаза 1) це був "god object": один клас робив читання WB Soul,
розрахунок репутації, policy, EIP-712 валідацію, релей у мережу і формування
відповіді. Фаза 2 розбила це на модулі з єдиною відповідальністю:

  identity.py    — читання WB Soul (soulOf + IsVerified + SBT) + репутація
  reputation.py  — формула score/tier (Компонент 3)
  policy.py      — allow/deny за identity + вимогами ресурсу
  payment.py     — офчейн EIP-712/EIP-3009 валідація
  settlement.py  — релей + форвард комісії (seam під atomic router у Фазі 2.5)
  events.py      — журнал подій платіжного циклу (Компонент 4)
  capability.py  — service discovery (Компонент 2)

Цей клас лише ТРИМАЄ спільний контекст (w3, контракти, store, модулі) і
ПОСЛІДОВНО їх викликає: identity -> policy -> payment -> settlement -> event
-> response. Жодної бізнес-логіки тут немає — тільки послідовність.

Зовнішній контракт verify_and_settle(...) і ключі результату збережені 1:1
з Фази 1, тож service_provider/server.py і всі тести Фази 1 працюють без змін.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chain  # noqa: E402
import config  # noqa: E402
from facilitator import events as events_mod  # noqa: E402
from facilitator import identity as identity_mod  # noqa: E402
from facilitator import policy as policy_mod  # noqa: E402
from facilitator import reputation as reputation_mod  # noqa: E402
from facilitator.events import EventLog  # noqa: E402
from facilitator.identity import IdentityReader  # noqa: E402
from facilitator.payment import PaymentValidator  # noqa: E402
from facilitator.settlement import SettlementEngine, SettlementError  # noqa: E402
from facilitator.store import Store  # noqa: E402

logger = logging.getLogger(__name__)


class WhitechainFacilitator:
    def __init__(self, w3: Web3 | None = None, store: Store | None = None):
        self.w3 = w3 or chain.get_w3()
        self._assert_correct_chain()

        self.store = store if store is not None else Store(config.STORE_DB_PATH)

        # --- контрактні хендли ---
        self.teurc = chain.get_contract(self.w3, "tEURC", config.TEURC_ADDRESS)
        soul_registry = chain.get_contract(self.w3, "ISoulRegistry", config.SOUL_REGISTRY_ADDRESS)
        attribute_registry = chain.get_contract(
            self.w3, "ISoulAttributeRegistry", config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS
        )
        sbt_registry = chain.get_contract(
            self.w3, "ISoulBoundTokenRegistry", config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS
        )

        # --- модулі (кожен з однією відповідальністю) ---
        self.identity = IdentityReader(
            soul_registry,
            attribute_registry,
            sbt_registry,
            config.IS_VERIFIED_ATTRIBUTE_ADDRESS,
            self.store,
            n_target=config.REPUTATION_N_TARGET,
        )
        self.payment = PaymentValidator(
            self.w3,
            self.teurc,
            facilitator_address=config.FACILITATOR_WALLET_ADDRESS,
            teurc_decimals=config.TEURC_DECIMALS,
        )
        self.settlement = SettlementEngine(
            self.w3,
            self.teurc,
            facilitator_private_key=config.FACILITATOR_WALLET_PRIVATE_KEY,
            service_provider_address=config.SERVICE_PROVIDER_WALLET_ADDRESS,
            fee_bps=config.FACILITATOR_FEE_BPS,
            teurc_decimals=config.TEURC_DECIMALS,
            wait_for_confirmation=config.WAIT_FOR_CONFIRMATION,
            confirmation_timeout=config.SETTLEMENT_CONFIRMATION_TIMEOUT,
        )
        self.events = EventLog(self.store)

    def _assert_correct_chain(self) -> None:
        """Fail fast на старті, якщо RPC веде не на той ланцюжок."""
        try:
            actual_chain_id = self.w3.eth.chain_id
        except Exception as exc:  # noqa: BLE001 — RPC недоступний на старті
            logger.error("Не вдалося підключитися до RPC на старті: %s", exc)
            raise RuntimeError(
                "Не вдалося підключитися до RPC, щоб перевірити мережу. Перевір "
                "WHITECHAIN_TESTNET_RPC/NETWORK у .env — RPC має бути доступний і відповідати."
            ) from None

        if config.NETWORK == "whitechain_testnet" and actual_chain_id != config.CHAIN_ID:
            raise RuntimeError(
                f"RPC веде на chain_id={actual_chain_id}, а очікується Whitechain "
                f"testnet chain_id={config.CHAIN_ID}. Перевір WHITECHAIN_TESTNET_RPC/CHAIN_ID у .env."
            )

    def get_agent_identity(self, address: str) -> dict:
        """Тонкий прокидач у identity-модуль (зручно для demo/тестів)."""
        return self.identity.get_agent_identity(address)

    def verify_and_settle(
        self,
        authorization: dict,
        resource: str,
        resource_salt: str,
        price_teurc: float,
        min_reputation_tier: int = 0,
    ) -> dict:
        """Оркеструє повний цикл: identity -> policy -> payment -> settlement
        -> event -> response. Сигнатура і ключі результату — як у Фазі 1.
        """
        from_addr = Web3.to_checksum_address(authorization["from"])
        emitted: list[dict] = []

        def emit(event_type: str, tx_hash: str | None = None) -> None:
            emitted.append(self.events.emit(event_type, agent=from_addr, resource=resource, tx_hash=tx_hash))

        emit(events_mod.PAYMENT_REQUESTED)

        # 1. Identity (WB Soul + reputation).
        agent_identity = self.identity.get_agent_identity(from_addr)

        # 2. Policy (KYA + reputation gate).
        decision = policy_mod.check_policy(
            agent_identity, {"min_reputation_tier": min_reputation_tier, "price_teurc": price_teurc}
        )
        if not decision["allow"]:
            return self._deny(decision["reason"], agent_identity, emitted)

        # 3. Payment (офчейн EIP-712/EIP-3009 валідація).
        validation = self.payment.validate_authorization(authorization, resource, resource_salt, price_teurc)
        if not validation["ok"]:
            return self._deny(validation["reason"], agent_identity, emitted)
        emit(events_mod.AUTHORIZATION_VALIDATED)

        # 4. Settlement (релей + форвард комісії).
        try:
            result = self.settlement.settle(validation["message"], authorization)
        except SettlementError as exc:
            logger.warning("Settlement провалився: %s", exc)
            return self._deny(f"Розрахунок не вдався: {exc}", agent_identity, emitted)
        emit(events_mod.SETTLEMENT_SUBMITTED, tx_hash=result["relay_tx_hash"])
        if result["confirmed"]:
            emit(events_mod.SETTLEMENT_CONFIRMED, tx_hash=result["relay_tx_hash"])

        # 5. Post-settlement: оновлюємо поведінкову статистику + reputation-anchor.
        self._record_completed_payment(from_addr, agent_identity)

        # 6. Access granted (ресурс видається на цьому кроці — після
        # SettlementConfirmed, якщо WAIT_FOR_CONFIRMATION=true).
        emit(events_mod.ACCESS_GRANTED, tx_hash=result["relay_tx_hash"])

        scale = 10**config.TEURC_DECIMALS
        return {
            "valid": True,
            "reason": "Оплату прийнято офчейн; розрахунок проведено.",
            "reputation_tier": agent_identity["reputation_tier"],
            "reputation_score": agent_identity["reputation_score"],
            "soul_id": agent_identity["soul_id"],
            "amount_teurc": validation["message"]["value"] / scale,
            "fee_teurc": result["fee_wei"] / scale,
            "net_to_service_provider_teurc": result["net_wei"] / scale,
            "relay_tx_hash": result["relay_tx_hash"],
            "forward_tx_hash": result["forward_tx_hash"],
            "settlement_status": result["status"],
            "events": emitted,
        }

    def _deny(self, reason: str, agent_identity: dict, emitted: list[dict]) -> dict:
        return {
            "valid": False,
            "reason": reason,
            "reputation_tier": agent_identity.get("reputation_tier") if agent_identity.get("has_soul") else None,
            "events": emitted,
        }

    def _record_completed_payment(self, address: str, agent_identity: dict) -> None:
        """Оновлює лічильник completed_payments і логує reputation-anchor,
        якщо агент перетнув поріг tier (реальний mint SBT — TODO/demo)."""
        tier_before = agent_identity["reputation_tier"]
        self.store.increment_completed_payment(address)

        # Перерахунок після інкремента — суто для anchor-логу (не для гейту цього запиту).
        new_identity = self.identity.get_agent_identity(address)
        tier_after = new_identity["reputation_tier"]
        if tier_after > tier_before:
            logger.info(
                "reputation-anchor: agent=%s crossed tier %d -> %d; SBT tier %d would be minted "
                "(MockSoulRegistry.issueSBT у demo; на реальному WB Soul — TODO)",
                address,
                tier_before,
                tier_after,
                tier_after,
            )
