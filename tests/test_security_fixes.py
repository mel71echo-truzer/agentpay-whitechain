"""Регресійні тести для фіксів M-1 (прив'язка оплати до ресурсу) і M-3
(fail-fast перевірка chain_id) з SECURITY_REVIEW.md.

Без реального RPC і без реальних ключів — усе на локальному EVM-ланцюжку
(web3 EthereumTesterProvider), як і tests/local_integration_test.py.

Запуск: python tests/test_security_fixes.py
"""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web3 import EthereumTesterProvider, Web3  # noqa: E402

import config  # noqa: E402


def test_chain_id_mismatch_fails_fast() -> None:
    """M-3: facilitator відмовляється стартувати проти не тієї мережі."""
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    test_w3 = Web3(EthereumTesterProvider())
    config.WHITECHAIN_CHAIN_ID = 999_999_999  # свідомо не той chain

    try:
        WhitechainFacilitator(w3=test_w3, used_tx_store=".test_m3_wrongchain.json")
        raise AssertionError("мало впасти на невідповідності chain_id")
    except RuntimeError as exc:
        assert "chain_id" in str(exc), f"повідомлення не пояснює причину: {exc}"

    # correct chain_id -> works fine
    config.WHITECHAIN_CHAIN_ID = test_w3.eth.chain_id
    fac = WhitechainFacilitator(w3=test_w3, used_tx_store=".test_m3_rightchain.json")
    assert fac is not None
    Path(".test_m3_rightchain.json").unlink(missing_ok=True)

    print("✅ test_chain_id_mismatch_fails_fast")


def test_payment_for_photo_a_cannot_unlock_photo_b() -> None:
    """M-1: валідний tx_hash, сплачений за фото A, не відкриває фото B."""
    import wallets.setup_wallets as sw
    from author.x402_client import pay_and_fetch
    from facilitator.whitechain_facilitator import WhitechainFacilitator
    import photobank.server as photobank_module

    test_w3 = Web3(EthereumTesterProvider())
    faucet = test_w3.eth.accounts[0]

    author_addr, author_key = sw.generate_wallet()
    photobank_addr, _ = sw.generate_wallet()
    test_w3.eth.send_transaction({"from": faucet, "to": author_addr, "value": Web3.to_wei(5, "ether")})

    config.WHITECHAIN_CHAIN_ID = test_w3.eth.chain_id
    config.PHOTOBANK_WALLET_ADDRESS = photobank_addr
    config.PHOTO_PRICE_WBT = 0.02

    photobank_module.facilitator = WhitechainFacilitator(
        w3=test_w3, used_tx_store=".test_m1_used_tx.json"
    )

    from fastapi.testclient import TestClient

    client = TestClient(photobank_module.app)

    # Автор платить конкретно за kyiv-lavra: назва ресурсу йде в calldata.
    resource_a = "/photo/kyiv-lavra"
    tx_hash = sw.send_wbt(
        author_key, photobank_addr, 0.02, test_w3, data=resource_a.encode("utf-8")
    )
    payment_header = base64.b64encode(json.dumps({"txHash": tx_hash}).encode()).decode()

    # Спроба пред'явити цей самий tx_hash за ІНШЕ фото — має провалитись.
    r_wrong = client.get("/photo/kyiv-sofia-cathedral", headers={"X-PAYMENT": payment_header})
    assert r_wrong.status_code == 402, f"мало бути 402, отримано {r_wrong.status_code}"
    assert "інший ресурс" in r_wrong.json()["error"] or "resource" in r_wrong.json()["error"].lower() \
        or "ресурс" in r_wrong.json()["error"], r_wrong.json()

    # Той самий tx_hash за ПРАВИЛЬНЕ фото — має спрацювати (happy path).
    r_right = client.get("/photo/kyiv-lavra", headers={"X-PAYMENT": payment_header})
    assert r_right.status_code == 200, f"мало бути 200, отримано {r_right.status_code}: {r_right.text}"
    assert r_right.headers["content-type"] == "image/png"

    # Повторна спроба тим самим tx_hash (уже використаний) — replay-захист.
    r_replay = client.get("/photo/kyiv-lavra", headers={"X-PAYMENT": payment_header})
    assert r_replay.status_code == 402
    assert "replay" in r_replay.json()["error"].lower() or "повтор" in r_replay.json()["error"].lower()

    Path(".test_m1_used_tx.json").unlink(missing_ok=True)
    print("✅ test_payment_for_photo_a_cannot_unlock_photo_b")


def test_happy_path_pay_and_fetch_end_to_end() -> None:
    """Контроль: звичайний pay_and_fetch() (клієнтський шлях) досі працює."""
    import wallets.setup_wallets as sw
    import author.x402_client as x402_client
    from author.x402_client import SpendLedger, pay_and_fetch
    from facilitator.whitechain_facilitator import WhitechainFacilitator
    import photobank.server as photobank_module
    import threading
    import time
    import uvicorn
    import requests

    test_w3 = Web3(EthereumTesterProvider())
    faucet = test_w3.eth.accounts[0]

    author_addr, author_key = sw.generate_wallet()
    photobank_addr, _ = sw.generate_wallet()
    test_w3.eth.send_transaction({"from": faucet, "to": author_addr, "value": Web3.to_wei(5, "ether")})

    config.WHITECHAIN_CHAIN_ID = test_w3.eth.chain_id
    config.PHOTOBANK_WALLET_ADDRESS = photobank_addr
    config.PHOTO_PRICE_WBT = 0.02
    config.PHOTOBANK_PORT = 8814
    config.PHOTOBANK_BASE_URL = "http://127.0.0.1:8814"

    photobank_module.facilitator = WhitechainFacilitator(
        w3=test_w3, used_tx_store=".test_m1_happy_used_tx.json"
    )

    _real_send_wbt = sw.send_wbt
    x402_client.send_wbt = lambda pk, to, amt, w3=None, data=None, _t=test_w3: _real_send_wbt(
        pk, to, amt, w3=w3 or _t, data=data
    )

    server = uvicorn.Server(
        uvicorn.Config(photobank_module.app, host="127.0.0.1", port=8814, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            requests.get("http://127.0.0.1:8814/photos", timeout=1)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.2)

    ledger = SpendLedger(path=".test_m1_happy_ledger.json", max_spend_wbt=1.0)
    result = pay_and_fetch(
        "http://127.0.0.1:8814/photo/kyiv-lavra",
        private_key=author_key,
        ledger=ledger,
        w3=test_w3,
    )
    assert result.tx_hash is not None
    assert result.amount_wbt == 0.02
    assert len(result.content) > 0

    server.should_exit = True
    time.sleep(0.3)
    Path(".test_m1_happy_used_tx.json").unlink(missing_ok=True)
    Path(".test_m1_happy_ledger.json").unlink(missing_ok=True)
    print("✅ test_happy_path_pay_and_fetch_end_to_end")


if __name__ == "__main__":
    test_chain_id_mismatch_fails_fast()
    test_payment_for_photo_a_cannot_unlock_photo_b()
    test_happy_path_pay_and_fetch_end_to_end()
    print("\nУсі регресійні тести M-1/M-3 пройшли ✅")
