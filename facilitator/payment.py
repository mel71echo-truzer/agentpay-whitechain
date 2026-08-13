"""Payment — офчейн-валідація EIP-712/EIP-3009 authorization (Фаза 2, Компонент 1).

Єдина відповідальність: перевірити, що authorization справжня і придатна
до релею, НЕ роблячи самого релею. Перевіряє (у тому ж порядку, що й Фаза 1,
щоб причини відмов не змінились):
  1. resource binding : nonce == keccak256(resource || salt)
  2. EIP-712 підпис    : recover(signature) == from
  3. часове вікно      : validAfter < now < validBefore
  4. anti-replay       : nonce не використаний on-chain (tEURC.authorizationState)
  5. отримувач + сума  : to == facilitator, value >= ціна ресурсу

Крок 4 читає стан контракту (джерело істини по nonce — сам tEURC), решта —
чисто офчейн. Повертає {"ok": bool, "reason": str, "message": dict|None}.
`message` — розібрані типізовані поля для settlement.py.
"""

from __future__ import annotations

import logging
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

import router_binding

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


class PaymentValidator:
    def __init__(
        self,
        w3,
        teurc,
        *,
        facilitator_address: str,
        teurc_decimals: int,
        teurc_name: str = "Test EURC",
        settlement_mode: str = "legacy",
        router_address: str | None = None,
        seller_address: str | None = None,
        fee_bps: int = 0,
    ):
        self.w3 = w3
        self.teurc = teurc
        self.facilitator_address = facilitator_address
        self.teurc_decimals = teurc_decimals
        self.teurc_name = teurc_name
        # atomic-режим: авторизація має бути ReceiveWithAuthorization на роутер,
        # а nonce — похідний (seller+feeBps+resourceHash). seller/fee_bps беремо
        # З КОНФІГУ facilitator-а (не з клієнта): підпис мусить збігтися саме з
        # НАШИМИ параметрами, інакше похідний nonce не зійдеться — reject.
        self.settlement_mode = settlement_mode.strip().lower()
        self.router_address = router_address
        self.seller_address = seller_address
        self.fee_bps = fee_bps
        if self.settlement_mode == "atomic" and not (router_address and seller_address):
            raise ValueError("atomic-режим PaymentValidator потребує router_address і seller_address.")

    def _eip712_domain(self) -> dict:
        return {
            "name": self.teurc_name,
            "version": "1",
            "chainId": self.w3.eth.chain_id,
            "verifyingContract": self.teurc.address,
        }

    def validate_authorization(self, authorization: dict, resource: str, resource_salt: str, price_wei: int) -> dict:
        atomic = self.settlement_mode == "atomic"
        from_addr = Web3.to_checksum_address(authorization["from"])

        # 1. resource binding.
        #    legacy: nonce == keccak(resource‖salt).
        #    atomic: nonce == computeNonce(seller, feeBps, keccak(resource‖salt)) —
        #    той самий спільний код (router_binding), що й у клієнта; будь-яка
        #    підміна seller/feeBps/ресурсу дає інший nonce і зривається тут.
        try:
            salt_bytes = _to_bytes32(resource_salt)
            given_nonce = _to_bytes32(authorization["nonce"])
        except (ValueError, TypeError):
            return {"ok": False, "reason": "Некоректний формат nonce/salt.", "message": None}

        resource_hash = router_binding.resource_hash(resource, salt_bytes)
        if atomic:
            expected_nonce = router_binding.compute_router_nonce(self.seller_address, self.fee_bps, resource_hash)
        else:
            expected_nonce = resource_hash
        if expected_nonce != given_nonce:
            return {
                "ok": False,
                "reason": "Авторизація призначена для іншого ресурсу (resource binding mismatch).",
                "message": None,
            }

        # 2. EIP-712 підпис.
        # Числові поля з недовіреного входу парсимо явно; нечислове значення —
        # чиста відмова, а не необроблений виняток нагору.
        try:
            value = int(authorization["value"])
            valid_after = int(authorization["validAfter"])
            valid_before = int(authorization["validBefore"])
        except (TypeError, ValueError, KeyError):
            return {"ok": False, "reason": "Некоректний тип числових полів авторизації.", "message": None}
        if value < 0:
            return {"ok": False, "reason": "Сума не може бути від'ємною.", "message": None}
        message = {
            "from": from_addr,
            "to": Web3.to_checksum_address(authorization["to"]),
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": given_nonce,
        }
        # atomic підписується над RECEIVE typehash (тільки payee=router сабмітить),
        # legacy — над TRANSFER. Домен tEURC той самий.
        auth_types = router_binding.RECEIVE_AUTH_TYPES if atomic else TRANSFER_AUTH_TYPES
        signable = encode_typed_data(self._eip712_domain(), auth_types, message)
        try:
            recovered = Account.recover_message(
                signable, vrs=(authorization["v"], authorization["r"], authorization["s"])
            )
        except Exception as exc:  # noqa: BLE001 — будь-яка погана вхідна структура
            logger.warning("Не вдалося відновити підписанта: %s", exc)
            return {"ok": False, "reason": "Некоректний підпис.", "message": None}

        if recovered.lower() != from_addr.lower():
            return {"ok": False, "reason": "Підпис не відповідає полю 'from'.", "message": None}

        # 3. Часове вікно.
        now = int(time.time())
        if now <= message["validAfter"]:
            return {"ok": False, "reason": "Авторизація ще не дійсна.", "message": None}
        if now >= message["validBefore"]:
            return {"ok": False, "reason": "Авторизація протермінована.", "message": None}

        # 4. Anti-replay (on-chain, джерело істини — сам контракт). tEURC веде
        # спільний реєстр spent-nonce для transfer/receive, тож перевірка єдина.
        if self.teurc.functions.authorizationState(from_addr, given_nonce).call():
            return {"ok": False, "reason": "Ця авторизація вже використана (replay).", "message": None}

        # 5. Отримувач + сума. У atomic отримувач — роутер, у legacy — facilitator.
        expected_to = self.router_address if atomic else self.facilitator_address
        if message["to"].lower() != expected_to.lower():
            reason = (
                "Авторизація призначена не на адресу router-а."
                if atomic
                else "Авторизація призначена не на адресу facilitator-а."
            )
            return {"ok": False, "reason": reason, "message": None}
        # price_wei приходить уже як int wei (конверсія — на межі, config/money).
        # Вимагаємо ТОЧНУ суму. Overpayment (value > price) відхиляємо, а не
        # приймаємо мовчки (рішення №5): у поточній архітектурі немає шляху
        # повернення надлишку — facilitator релеїть повний підписаний value,
        # тож переплата застрягла б у facilitator без вороття до платника (той
        # самий клас, що й «утримані кошти»). Без return-path — тільки відмова.
        if message["value"] < price_wei:
            return {
                "ok": False,
                "reason": f"Сума замала: {message['value']} < {price_wei} (потрібна ціна ресурсу).",
                "message": None,
            }
        if message["value"] > price_wei:
            return {
                "ok": False,
                "reason": (
                    f"Переплата: {message['value']} > {price_wei}. Надлишок не повертається — "
                    "підпиши авторизацію рівно на ціну ресурсу."
                ),
                "message": None,
            }

        if atomic:
            # Атомарний backend (settle) звертається до router.settlePaymentAtomic,
            # який сам перерахує nonce з (seller, feeBps, resourceHash). Кладемо ці
            # поля у message, щоб СИГНАТУРА settle(message, authorization) не мінялась
            # (seam лишається той самий — див. settlement.py docstring).
            message["seller"] = Web3.to_checksum_address(self.seller_address)
            message["feeBps"] = self.fee_bps
            message["resourceHash"] = resource_hash

        return {"ok": True, "reason": "OK", "message": message}
