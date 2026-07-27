"""x402-facilitator для Whitechain — Фаза 1: KYA-гейт + reputation + EIP-3009.

Що змінилось порівняно з Фазою 0 (нативний WBT + txHash):
- Оплата тепер у tEURC (ERC-20, EIP-3009) замість нативного WBT. Клієнт
  підписує authorization ОФЧЕЙН (EIP-712) — facilitator валідує підпис
  локально, без жодного виклику мережі, і тільки ПІСЛЯ повної офчейн-
  валідації релеїть transferWithAuthorization у мережу.
- Перед будь-якою перевіркою суми/підпису йде KYA-гейт: платник має мати
  верифікований WB Soul. Без цього — відмова, незалежно від суми чи підпису.
- Читається reputation агента (кількість SBT, прив'язаних до soul) —
  сервіс може вимагати мінімальний tier для преміум-ресурсів.
- Facilitator бере комісію (config.FACILITATOR_FEE_BPS, за замовчуванням
  0.5%) і форвардить решту AI Service Provider-у окремим переказом (простіший
  робочий варіант із двох, описаних у README/DEPLOY — без окремого
  router-контракту, ціною двох послідовних транзакцій замість однієї
  атомарної).
- Контент видається одразу після успішної офчейн-валідації + релею в
  мережу (широкосяг, без wait_for_transaction_receipt) — не чекаємо
  підтвердження блоку в циклі запиту. Це свідомий trade-off, задокументований
  у README: якщо релей пізніше провалиться (напр. нестача gas у
  facilitator-а), контент уже буде видано без фактичної оплати. Прийнятно
  для PoC; для продакшену треба або чекати підтвердження, або мати
  страхувальний механізм (ретраї/моніторинг провалених релеїв).

KYA/reputation читаються через ОДИН і той самий інтерфейс незалежно від
того, чи це MockSoulRegistry (config.USE_MOCK_SOUL=true) чи справжні WB
Soul контракти на Whitechain (USE_MOCK_SOUL=false) — інтерфейси
ISoulRegistry/ISoulAttributeRegistry/ISoulBoundTokenRegistry скопійовані
1:1 з https://github.com/whitebit-exchange/soul-ecosystem-contracts.
"""

import logging
import sys
import time as _time
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chain  # noqa: E402
import config  # noqa: E402

logger = logging.getLogger(__name__)

# EIP-712 типи для tEURC.TransferWithAuthorization — мають збігатися з
# TRANSFER_WITH_AUTHORIZATION_TYPEHASH у contracts/tEURC.sol.
TRANSFER_AUTH_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


def _to_bytes32(value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return bytes.fromhex(value.removeprefix("0x"))


class WhitechainFacilitator:
    def __init__(self, w3: Web3 | None = None):
        self.w3 = w3 or chain.get_w3()
        self._assert_correct_chain()

        self.teurc = chain.get_contract(self.w3, "tEURC", config.TEURC_ADDRESS)
        self.soul_registry = chain.get_contract(self.w3, "ISoulRegistry", config.SOUL_REGISTRY_ADDRESS)
        self.attribute_registry = chain.get_contract(
            self.w3, "ISoulAttributeRegistry", config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS
        )
        self.sbt_registry = chain.get_contract(
            self.w3, "ISoulBoundTokenRegistry", config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS
        )
        self.is_verified_attribute = Web3.to_checksum_address(config.IS_VERIFIED_ATTRIBUTE_ADDRESS)

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

    def _eip712_domain(self) -> dict:
        return {
            "name": "Test EURC",
            "version": "1",
            "chainId": self.w3.eth.chain_id,
            "verifyingContract": self.teurc.address,
        }

    def check_kya(self, address: str) -> dict:
        """KYA-гейт + reputation-читання для `address`.

        Повертає {"ok": True, "soul_id":..., "reputation_tier":...}
        або {"ok": False, "reason": "..."}.
        """
        address = Web3.to_checksum_address(address)
        soul_id = self.soul_registry.functions.soulOf(address).call()
        if soul_id == 0:
            return {"ok": False, "reason": "Агент не має верифікованого WB Soul (not KYA-verified)."}

        raw_value = self.attribute_registry.functions.soulAttributeValue(
            soul_id, self.is_verified_attribute
        ).call()
        verified = bytes(raw_value) != b"\x00" * 20
        if not verified:
            return {"ok": False, "reason": "KYC агента не активний (KYA not active)."}

        sbt_count = self.sbt_registry.functions.tokensCountBySoul(soul_id).call()
        reputation_tier = 0 if sbt_count == 0 else (1 if sbt_count == 1 else 2)

        return {"ok": True, "soul_id": soul_id, "reputation_tier": reputation_tier}

    def verify_and_settle(
        self,
        authorization: dict,
        resource: str,
        resource_salt: str,
        price_teurc: float,
        min_reputation_tier: int = 0,
    ) -> dict:
        """Перевіряє KYA + reputation + офчейн-підпис + прив'язку до ресурсу,
        і якщо все гаразд — релеїть оплату в мережу та форвардить комісію.

        authorization: {"from","to","value","validAfter","validBefore","nonce","v","r","s"}
        resource_salt: hex-рядок (32 байти) — разом з `resource` дає nonce
            (keccak256(resource || salt)), який має збігатися з
            authorization["nonce"]. Це прив'язує саме цю авторизацію до
            саме цього ресурсу (той самий принцип, що calldata-прив'язка
            у Фазі 0, адаптований під EIP-3009, де немає вільного data-поля).

        Повертає dict з "valid" (bool), "reason", і — де застосовно —
        "reputation_tier", "soul_id", txhashes і суми.
        """
        from_addr = Web3.to_checksum_address(authorization["from"])

        # 1. KYA-гейт — перед будь-чим іншим.
        kya = self.check_kya(from_addr)
        if not kya["ok"]:
            return {"valid": False, "reason": kya["reason"]}
        reputation_tier = kya["reputation_tier"]

        # 2. Reputation-гейт (якщо ресурс його вимагає).
        if reputation_tier < min_reputation_tier:
            return {
                "valid": False,
                "reason": (
                    f"Недостатня репутація: tier {reputation_tier} < потрібно "
                    f"{min_reputation_tier} (insufficient reputation)."
                ),
                "reputation_tier": reputation_tier,
            }

        # 3. Прив'язка до ресурсу: nonce має дорівнювати keccak256(resource || salt).
        try:
            salt_bytes = _to_bytes32(resource_salt)
            given_nonce = _to_bytes32(authorization["nonce"])
        except ValueError:
            return {"valid": False, "reason": "Некоректний формат nonce/salt.", "reputation_tier": reputation_tier}

        expected_nonce = Web3.keccak(resource.encode("utf-8") + salt_bytes)
        if expected_nonce != given_nonce:
            return {
                "valid": False,
                "reason": "Авторизація призначена для іншого ресурсу (resource binding mismatch).",
                "reputation_tier": reputation_tier,
            }

        # 4. Офчейн-валідація підпису — жодного виклику мережі.
        message = {
            "from": from_addr,
            "to": Web3.to_checksum_address(authorization["to"]),
            "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]),
            "validBefore": int(authorization["validBefore"]),
            "nonce": given_nonce,
        }
        signable = encode_typed_data(self._eip712_domain(), TRANSFER_AUTH_TYPES, message)
        try:
            recovered = Account.recover_message(
                signable, vrs=(authorization["v"], authorization["r"], authorization["s"])
            )
        except Exception as exc:  # noqa: BLE001 — навмисно широкий: будь-яка погана вхідна структура
            logger.warning("Не вдалося відновити підписанта: %s", exc)
            return {"valid": False, "reason": "Некоректний підпис.", "reputation_tier": reputation_tier}

        if recovered.lower() != from_addr.lower():
            return {"valid": False, "reason": "Підпис не відповідає полю 'from'.", "reputation_tier": reputation_tier}

        # 5. Часове вікно — та сама умова, що й у контракті (тут — щоб
        # відмовити швидко, без витрат на завідомо марний релей).
        now = int(_time.time())
        if now <= message["validAfter"]:
            return {"valid": False, "reason": "Авторизація ще не дійсна.", "reputation_tier": reputation_tier}
        if now >= message["validBefore"]:
            return {"valid": False, "reason": "Авторизація протермінована.", "reputation_tier": reputation_tier}

        # 6. Anti-replay: nonce не використаний ОНЧЕЙН (джерело істини — сам контракт).
        if self.teurc.functions.authorizationState(from_addr, given_nonce).call():
            return {"valid": False, "reason": "Ця авторизація вже використана (replay).", "reputation_tier": reputation_tier}

        # 7. Сума і отримувач.
        if message["to"].lower() != config.FACILITATOR_WALLET_ADDRESS.lower():
            return {
                "valid": False,
                "reason": "Авторизація призначена не на адресу facilitator-а.",
                "reputation_tier": reputation_tier,
            }
        price_wei = round(price_teurc * (10**config.TEURC_DECIMALS))
        if message["value"] < price_wei:
            return {
                "valid": False,
                "reason": f"Сума замала: {message['value']} < {price_wei} (потрібна ціна ресурсу).",
                "reputation_tier": reputation_tier,
            }

        # Усі перевірки пройдено офчейн — релеїмо в мережу. Не чекаємо
        # майнінгу: send_contract_tx лише розсилає (broadcast) і повертає
        # хеш транзакції.
        relay_tx_hash = chain.send_contract_tx(
            self.w3,
            config.FACILITATOR_WALLET_PRIVATE_KEY,
            self.teurc.functions.transferWithAuthorization(
                from_addr,
                message["to"],
                message["value"],
                message["validAfter"],
                message["validBefore"],
                given_nonce,
                authorization["v"],
                authorization["r"],
                authorization["s"],
            ),
        )

        fee_wei = (message["value"] * config.FACILITATOR_FEE_BPS) // 10_000
        net_wei = message["value"] - fee_wei

        forward_tx_hash = None
        if net_wei > 0 and config.SERVICE_PROVIDER_WALLET_ADDRESS:
            forward_tx_hash = chain.send_contract_tx(
                self.w3,
                config.FACILITATOR_WALLET_PRIVATE_KEY,
                self.teurc.functions.transfer(
                    Web3.to_checksum_address(config.SERVICE_PROVIDER_WALLET_ADDRESS), net_wei
                ),
            )

        scale = 10**config.TEURC_DECIMALS
        return {
            "valid": True,
            "reason": "Оплату прийнято офчейн; релей у мережу відправлено.",
            "reputation_tier": reputation_tier,
            "soul_id": kya["soul_id"],
            "amount_teurc": message["value"] / scale,
            "fee_teurc": fee_wei / scale,
            "net_to_service_provider_teurc": net_wei / scale,
            "relay_tx_hash": relay_tx_hash,
            "forward_tx_hash": forward_tx_hash,
        }
