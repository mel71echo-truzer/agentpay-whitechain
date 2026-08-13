"""Спільні хелпери прив'язки для атомарного settlement-шляху (Фаза 2.5).

Атомарний роутер (contracts/AgentPayRouter.sol) закриває C-1 тим, що вшиває
seller + feeBps + resourceHash у EIP-3009 nonce, який підписує покупець, а сам
покупець підписує **ReceiveWithAuthorization** (to == router, тобто тільки
роутер-payee може сабмітити в tEURC). Дві сторони мусять вивести ТОЙ САМИЙ
nonce — клієнт (agent_client.py) при підписі й валідатор фасилітатора
(facilitator/payment.py) при перевірці. Якщо вони розійдуться хоч на байт,
ECDSA-recovery у tEURC перестане повертати `from` і токен зробить revert.

Тримаємо обидва деривації в ОДНОМУ модулі, щоб між клієнтом і фасилітатором
не могло виникнути розходження. Логіку C-1 тут НЕ послаблюємо — лише
дзеркалимо Solidity computeNonce байт-у-байт.
"""

from __future__ import annotations

from eth_abi import encode as _abi_encode
from web3 import Web3

# EIP-712 типи для tEURC.receiveWithAuthorization (RECEIVE_WITH_AUTHORIZATION_TYPEHASH).
# Ті самі 6 полів, що й у TransferWithAuthorization, але інший primaryType — тож
# recovery йде над іншим typehash, і тільки payee (to == router) може сабмітити.
RECEIVE_AUTH_TYPES = {
    "ReceiveWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


def resource_hash(resource: str, salt: bytes) -> bytes:
    """keccak256(resource ‖ salt) — прив'язка платежу до конкретного ресурсу.

    Це та сама база, що й legacy-nonce (keccak(resource‖salt)); у атомарному
    режимі вона стає ОДНИМ із входів computeNonce, а не самим nonce."""
    return Web3.keccak(resource.encode("utf-8") + salt)


def compute_router_nonce(seller: str, fee_bps: int, resource_hash_bytes: bytes) -> bytes:
    """Дзеркало AgentPayRouter.computeNonce:
    keccak256(abi.encode(address seller, uint16 feeBps, bytes32 resourceHash)).

    Мусить збігатися з Solidity байт-у-байт: адреса кодується незалежно від
    checksum-регістру (20→32 байти), feeBps як uint16 (32 байти), resourceHash
    як bytes32. Будь-яке відхилення → tEURC-recovery не дає `from` → revert."""
    return Web3.keccak(
        _abi_encode(
            ["address", "uint16", "bytes32"],
            [Web3.to_checksum_address(seller), int(fee_bps), bytes(resource_hash_bytes)],
        )
    )
