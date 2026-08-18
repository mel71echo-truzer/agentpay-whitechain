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
import money  # noqa: E402
from facilitator import events as events_mod  # noqa: E402
from facilitator import policy as policy_mod  # noqa: E402
from facilitator.events import EventLog  # noqa: E402
from facilitator.identity import IdentityReader  # noqa: E402
from facilitator.atomic_settlement import AtomicSettlementEngine  # noqa: E402
from facilitator.payment import PaymentValidator  # noqa: E402
from facilitator.settlement import SettlementEngine, SettlementError, SettlementForwardError  # noqa: E402
from facilitator import store as store_mod  # noqa: E402
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
        # --- payment + settlement: обираємо backend за SETTLEMENT_MODE ---
        # Обидва тримають ОДИН seam: validate_authorization(...) -> message і
        # settle(message, authorization) -> {6 ключів}. Клас-оркестратор нижче
        # (verify_and_settle) НЕ знає, який backend активний — код той самий.
        if config.SETTLEMENT_MODE == "atomic":
            # Атомарний шлях: підпис ReceiveWithAuthorization на роутер, похідний
            # nonce (C-1), розрахунок однією tx (без вікна funds-held).
            router = chain.get_contract(self.w3, "AgentPayRouter", config.ROUTER_ADDRESS)
            self.payment = PaymentValidator(
                self.w3,
                self.teurc,
                facilitator_address=config.FACILITATOR_WALLET_ADDRESS,
                teurc_decimals=config.TEURC_DECIMALS,
                settlement_mode="atomic",
                router_address=config.ROUTER_ADDRESS,
                seller_address=config.SERVICE_PROVIDER_WALLET_ADDRESS,
                fee_bps=config.FACILITATOR_FEE_BPS,
            )
            self.settlement = AtomicSettlementEngine(
                self.w3,
                self.teurc,
                router,
                facilitator_private_key=config.FACILITATOR_WALLET_PRIVATE_KEY,
                treasury_address=config.TREASURY_ADDRESS,
                fee_bps=config.FACILITATOR_FEE_BPS,
                teurc_decimals=config.TEURC_DECIMALS,
                wait_for_confirmation=config.WAIT_FOR_CONFIRMATION,
                confirmation_timeout=config.SETTLEMENT_CONFIRMATION_TIMEOUT,
            )
        else:
            # legacy (дефолт під час розробки): офчейн relay + forward нетто.
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
        price_wei: int,
        min_reputation_tier: int = 0,
    ) -> dict:
        """Оркеструє повний цикл: identity -> policy -> payment -> settlement
        -> event -> response. Гроші — int wei (рішення №4): price_wei приходить
        уже сконвертованим на межі (config/money), у цьому шляху float немає.
        Відповідь несе авторитетні *_wei (int) + людські *_teurc (рядок).
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
            agent_identity, {"min_reputation_tier": min_reputation_tier, "price_wei": price_wei}
        )
        if not decision["allow"]:
            return self._deny(decision["reason"], agent_identity, emitted)

        # 3. Payment (офчейн EIP-712/EIP-3009 валідація).
        validation = self.payment.validate_authorization(authorization, resource, resource_salt, price_wei)
        if not validation["ok"]:
            return self._deny(validation["reason"], agent_identity, emitted)
        emit(events_mod.AUTHORIZATION_VALIDATED)

        # 4. Settlement (релей + форвард комісії).
        # Журнал ПЕРЕД бродкастом (рішення №3): фіксуємо намір рушити кошти
        # ДО будь-якої транзакції, щоб «завислий» розрахунок був відновлюваний.
        emit(events_mod.SETTLEMENT_INITIATED)
        try:
            result = self.settlement.settle(validation["message"], authorization)
        except SettlementForwardError as exc:
            # ЧАСТКОВИЙ збій: релей пройшов (кошти у facilitator), форвард — ні.
            # Явний, придатний для звірки стан «кошти утримані, зобов'язання
            # не виконане». БЕЗ авто-ретраю (рішення №3): tx_hash релею
            # журналюється для ручної/окремої реконсиляції. Доступ НЕ видаємо.
            logger.error(
                "Частковий збій сеттлменту: кошти утримані relay=%s net_wei=%s: %s",
                exc.relay_tx_hash, exc.net_wei, exc,
            )
            emit(events_mod.SETTLEMENT_FUNDS_HELD, tx_hash=exc.relay_tx_hash)
            # M-3: персиститься достатньо, щоб held був АДРЕСОВАНИЙ пізніше
            # (сума/продавець/покупець/nonce), а не лише tx_hash у логах.
            # Ідемпотентно за relay_tx_hash. Це ОБЛІК — не рух коштів.
            self.store.record_held(
                exc.relay_tx_hash,
                net_wei=exc.net_wei,
                seller=config.SERVICE_PROVIDER_WALLET_ADDRESS,
                buyer=from_addr,
                nonce=self._nonce_str(validation["message"]["nonce"]),
            )
            return self._deny(
                "Кошти утримані, зобов'язання не виконане (форвард сервісу "
                "відкотився). Розрахунок зафіксовано для звірки.",
                agent_identity, emitted,
            )
        except SettlementError as exc:
            # Чистий збій: релей не пройшов, кошти не рухалися.
            # Повний текст винятку (може нести внутрішні RPC/revert-деталі) — ЛИШЕ
            # в лог; клієнту — узагальнена причина без str(exc) (F-01).
            logger.warning("Settlement провалився (кошти не рухалися): %s", exc)
            emit(events_mod.SETTLEMENT_FAILED)
            return self._deny("Внутрішня помилка розрахунку, спробуйте пізніше.", agent_identity, emitted)
        emit(events_mod.SETTLEMENT_SUBMITTED, tx_hash=result["relay_tx_hash"])
        if result["confirmed"]:
            emit(events_mod.SETTLEMENT_CONFIRMED, tx_hash=result["relay_tx_hash"])

        # 5. Post-settlement: оновлюємо поведінкову статистику + reputation-anchor.
        self._record_completed_payment(from_addr, agent_identity)

        # 6. Access granted (ресурс видається на цьому кроці — після
        # SettlementConfirmed, якщо WAIT_FOR_CONFIRMATION=true).
        emit(events_mod.ACCESS_GRANTED, tx_hash=result["relay_tx_hash"])

        dec = config.TEURC_DECIMALS
        amount_wei = validation["message"]["value"]
        return {
            "valid": True,
            "reason": "Оплату прийнято офчейн; розрахунок проведено.",
            "reputation_tier": agent_identity["reputation_tier"],
            "reputation_score": agent_identity["reputation_score"],
            "soul_id": agent_identity["soul_id"],
            # Авторитетні суми — int wei; людські *_teurc — рядок (без float).
            "amount_wei": amount_wei,
            "fee_wei": result["fee_wei"],
            "net_wei": result["net_wei"],
            "amount_teurc": money.wei_to_teurc_str(amount_wei, dec),
            "fee_teurc": money.wei_to_teurc_str(result["fee_wei"], dec),
            "net_to_service_provider_teurc": money.wei_to_teurc_str(result["net_wei"], dec),
            "relay_tx_hash": result["relay_tx_hash"],
            "forward_tx_hash": result["forward_tx_hash"],
            "settlement_status": result["status"],
            "events": emitted,
        }

    def list_held_settlements(self, limit: int = 100) -> list[dict]:
        """Звірка (рішення №3): розрахунки з утриманими коштами (релей пройшов,
        форвард відкотився). Кожен запис несе tx_hash релею → операційна
        команда може простежити суму on-chain і провести форвард вручну.
        БЕЗ авто-ретраю — це навмисно окреме рішення, не сайд-ефект."""
        return self.store.list_events(event_type=events_mod.SETTLEMENT_FUNDS_HELD, limit=limit)

    # ==================== M-3: reconciliation ====================
    # Облік + повторна перевірка + ідемпотентний стан-машина. НЕ фейкова гарантія
    # повернення: facilitator фізично тримає net_wei (стандартний ERC-20) і може
    # переслати його продавцю (forward) або повернути покупцю (refund). Кожен рух
    # коштів — ЯВНА операторська дія, з on-chain перевіркою ПЕРЕД переказом і
    # переходом у RESOLVED_* ЛИШЕ після фактичного on-chain підтвердження
    # (паритет з M-2: жодного «resolved» без підтвердження). `.sol`, atomic-шлях і
    # авто-ретрай НЕ чіпаються.

    @staticmethod
    def _nonce_str(nonce) -> str:
        """Стабільний рядковий вигляд EIP-3009 nonce для обліку (bytes32|hex|str)."""
        if isinstance(nonce, (bytes, bytearray)):
            return "0x" + bytes(nonce).hex()
        return str(nonce)

    def list_reconciliation(self, *, state: str | None = None, limit: int = 100) -> list[dict]:
        """Повний облік звірки (усі стани або відфільтрований). На відміну від
        list_held_settlements (сирий журнал подій) — це стан-машина з полями
        net_wei/seller/buyer/nonce/state/action_tx_hash."""
        return self.store.list_reconciliation(state=state, limit=limit)

    def _receipt_status(self, tx_hash: str | None) -> str:
        """('confirmed'|'reverted'|'pending') за on-chain receipt. Відсутній
        receipt => 'pending' (ще не змайнено). Ключ self-heal + анти-подвійного руху."""
        from web3.exceptions import TransactionNotFound

        if not tx_hash:
            return "pending"
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return "pending"
        return "confirmed" if receipt.status == 1 else "reverted"

    def _confirm_tx(self, tx_hash: str) -> bool:
        """Чекає підтвердження в межах вікна. True лише при status==1. Таймаут/
        відкат => False (розрахунок лишається *_SUBMITTED і придатний до повторної
        звірки — без нового переказу, якщо tx насправді пройшов: див. self-heal)."""
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=config.SETTLEMENT_CONFIRMATION_TIMEOUT
            )
        except Exception:  # noqa: BLE001 — таймаут/RPC: не термінально, лишаємось SUBMITTED
            return False
        return receipt.status == 1

    def _broadcast_transfer(self, to_address: str, amount_wei: int) -> str:
        """facilitator → to_address на amount_wei tEURC (стандартний ERC-20 transfer)."""
        return chain.send_contract_tx(
            self.w3,
            config.FACILITATOR_WALLET_PRIVATE_KEY,
            self.teurc.functions.transfer(Web3.to_checksum_address(to_address), int(amount_wei)),
        )

    @staticmethod
    def _recon_result(rec: dict, *, ok: bool, changed: bool, moved_funds: bool, detail: str, action: str, dry_run: bool) -> dict:
        return {
            "ok": ok,
            "action": action,
            "dry_run": dry_run,
            "relay_tx_hash": rec["relay_tx_hash"],
            "state": rec["state"],
            "changed": changed,
            "moved_funds": moved_funds,
            "action_tx_hash": rec.get("action_tx_hash"),
            "net_wei": rec["net_wei"],
            "seller": rec["seller"],
            "buyer": rec["buyer"],
            "detail": detail,
        }

    def reconcile_held(self, relay_tx_hash: str, action: str, *, dry_run: bool = True) -> dict:
        """Керована звірка одного утриманого розрахунку. `action` ∈
        {'forward','refund_request','refund_execute'}. `dry_run=True` (дефолт)
        НІЧОГО не рухає й нічого не змінює — лише показує намір. Ідемпотентно:
        повторний виклик у термінальному стані = no-op без руху коштів."""
        S = store_mod
        if action not in ("forward", "refund_request", "refund_execute"):
            return {"ok": False, "action": action, "dry_run": dry_run,
                    "relay_tx_hash": relay_tx_hash, "detail": f"Невідома дія '{action}'."}

        rec = self.store.get_reconciliation(relay_tx_hash)
        if rec is None:
            return {"ok": False, "action": action, "dry_run": dry_run,
                    "relay_tx_hash": relay_tx_hash, "detail": "Невідомий утриманий розрахунок."}
        state = rec["state"]

        # ---------------- forward ----------------
        if action == "forward":
            if state == S.RECON_RESOLVED_FORWARDED:
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail="Уже переслано (idempotent no-op).", action=action, dry_run=dry_run)
            if state in (S.RECON_RESOLVED_REFUNDED, S.RECON_REFUND_PENDING, S.RECON_REFUND_SUBMITTED):
                return self._recon_result(rec, ok=False, changed=False, moved_funds=False,
                                          detail=f"Розрахунок на refund-треку ({state}); forward неможливий.",
                                          action=action, dry_run=dry_run)
            # state ∈ {HELD, FORWARD_SUBMITTED}: спершу on-chain re-check попередньої спроби.
            prior = self._receipt_status(rec["action_tx_hash"])
            if prior == "confirmed":
                rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_RESOLVED_FORWARDED)
                return self._recon_result(rec, ok=True, changed=True, moved_funds=False,
                                          detail="Self-heal: попередній forward підтверджено on-chain (без нового переказу).",
                                          action=action, dry_run=dry_run)
            if prior == "pending" and rec["action_tx_hash"]:
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail="Попередній forward ще в мемпулі; повторно НЕ розсилаю.",
                                          action=action, dry_run=dry_run)
            # prior == 'reverted' або action_tx_hash відсутній -> можна (пере)розсилати.
            if dry_run:
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail=f"DRY RUN: переказав би net_wei={rec['net_wei']} продавцю {rec['seller']}.",
                                          action=action, dry_run=dry_run)
            tx = self._broadcast_transfer(rec["seller"], rec["net_wei"])
            rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_FORWARD_SUBMITTED, action_tx_hash=tx)
            if self._confirm_tx(tx):
                rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_RESOLVED_FORWARDED)
                return self._recon_result(rec, ok=True, changed=True, moved_funds=True,
                                          detail="Forward підтверджено on-chain.", action=action, dry_run=dry_run)
            return self._recon_result(rec, ok=True, changed=True, moved_funds=True,
                                      detail="Forward розіслано; очікує підтвердження (лишається SUBMITTED).",
                                      action=action, dry_run=dry_run)

        # ---------------- refund_request (лише заявка, БЕЗ переказу) ----------------
        if action == "refund_request":
            if state == S.RECON_RESOLVED_REFUNDED:
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail="Уже повернено (idempotent no-op).", action=action, dry_run=dry_run)
            if state in (S.RECON_REFUND_PENDING, S.RECON_REFUND_SUBMITTED):
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail="Заявку на повернення вже створено (idempotent no-op).",
                                          action=action, dry_run=dry_run)
            if state == S.RECON_RESOLVED_FORWARDED:
                return self._recon_result(rec, ok=False, changed=False, moved_funds=False,
                                          detail="Уже переслано продавцю; refund неможливий.", action=action, dry_run=dry_run)
            if state == S.RECON_FORWARD_SUBMITTED:
                return self._recon_result(rec, ok=False, changed=False, moved_funds=False,
                                          detail="Forward у польоті; спершу заверши/відкоти його.", action=action, dry_run=dry_run)
            # state == HELD -> лише створюємо заявку. ЖОДНОГО переказу.
            if dry_run:
                return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                          detail="DRY RUN: створив би заявку на повернення (без переказу).",
                                          action=action, dry_run=dry_run)
            rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_REFUND_PENDING)
            return self._recon_result(rec, ok=True, changed=True, moved_funds=False,
                                      detail="Заявку на повернення створено. Виконання — окремим refund_execute.",
                                      action=action, dry_run=dry_run)

        # ---------------- refund_execute (окреме, свідоме виконання) ----------------
        # Виклик refund_execute = операторське підтвердження виконання заявки.
        if state == S.RECON_RESOLVED_REFUNDED:
            return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                      detail="Уже повернено (idempotent no-op).", action=action, dry_run=dry_run)
        if state == S.RECON_RESOLVED_FORWARDED:
            return self._recon_result(rec, ok=False, changed=False, moved_funds=False,
                                      detail="Уже переслано продавцю; refund неможливий.", action=action, dry_run=dry_run)
        if state in (S.RECON_HELD, S.RECON_FORWARD_SUBMITTED):
            return self._recon_result(rec, ok=False, changed=False, moved_funds=False,
                                      detail="Повернення не заявлено; спершу refund_request (окрема політика).",
                                      action=action, dry_run=dry_run)
        # state ∈ {REFUND_PENDING, REFUND_SUBMITTED}: on-chain re-check попередньої спроби.
        prior = self._receipt_status(rec["action_tx_hash"])
        if prior == "confirmed":
            rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_RESOLVED_REFUNDED)
            return self._recon_result(rec, ok=True, changed=True, moved_funds=False,
                                      detail="Self-heal: попереднє повернення підтверджено on-chain (без нового переказу).",
                                      action=action, dry_run=dry_run)
        if prior == "pending" and rec["action_tx_hash"]:
            return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                      detail="Попереднє повернення ще в мемпулі; повторно НЕ розсилаю.",
                                      action=action, dry_run=dry_run)
        if dry_run:
            return self._recon_result(rec, ok=True, changed=False, moved_funds=False,
                                      detail=f"DRY RUN: повернув би net_wei={rec['net_wei']} покупцю {rec['buyer']}.",
                                      action=action, dry_run=dry_run)
        tx = self._broadcast_transfer(rec["buyer"], rec["net_wei"])
        rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_REFUND_SUBMITTED, action_tx_hash=tx)
        if self._confirm_tx(tx):
            rec = self.store.update_reconciliation(relay_tx_hash, state=S.RECON_RESOLVED_REFUNDED)
            return self._recon_result(rec, ok=True, changed=True, moved_funds=True,
                                      detail="Повернення підтверджено on-chain.", action=action, dry_run=dry_run)
        return self._recon_result(rec, ok=True, changed=True, moved_funds=True,
                                  detail="Повернення розіслано; очікує підтвердження (лишається SUBMITTED).",
                                  action=action, dry_run=dry_run)

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
