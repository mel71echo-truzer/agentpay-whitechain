"""
Facilitator — сервіс-посередник x402 на Whitechain.
Приймає підписану авторизацію EIP-3009 і:
  /verify  — перевіряє підпис off-chain (без транзакції, безкоштовно)
  /settle  — відправляє transferWithAuthorization on-chain (виконує переказ)

Запуск:  uvicorn x402.facilitator:app --port 8001
Потрібно у .env: RPC_URL, CHAIN_ID, TOKEN_ADDRESS, FACILITATOR_PRIVATE_KEY
"""
import os
from fastapi import FastAPI
from pydantic import BaseModel
from web3 import Web3

# Відома пастка PoA-мереж (як Whitechain): web3 падає з помилкою extraData.
# Лікується middleware. Імпорт сумісний з різними версіями web3.py.
try:
    from web3.middleware import geth_poa_middleware  # web3 < 7
except ImportError:  # web3 >= 7
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware

from x402 import common

RPC_URL = common.RPC_URL
CHAIN_ID = common.CHAIN_ID
TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", common.TOKEN_ADDRESS)
FACILITATOR_PK = os.getenv("FACILITATOR_PRIVATE_KEY", os.getenv("PRIVATE_KEY", ""))

# Мінімальний ABI — лише те, що нам треба.
TOKEN_ABI = [
    {"name": "transferWithAuthorization", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
        {"name": "v", "type": "uint8"}, {"name": "r", "type": "bytes32"}, {"name": "s", "type": "bytes32"}],
     "outputs": []},
    {"name": "authorizationState", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "authorizer", "type": "address"}, {"name": "nonce", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

app = FastAPI(title="AgentPay Facilitator")


def get_w3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


class SettleRequest(BaseModel):
    authorization: dict
    signature: str


def _split_sig(signature_hex: str):
    sig = bytes.fromhex(signature_hex[2:] if signature_hex.startswith("0x") else signature_hex)
    r = sig[0:32]; s = sig[32:64]; v = sig[64]
    if v < 27:
        v += 27
    return v, r, s


@app.post("/verify")
def verify(req: SettleRequest):
    """Off-chain перевірка: чи справді власник коштів підписав цю авторизацію."""
    signer = common.recover_signer(TOKEN_ADDRESS, CHAIN_ID, req.authorization, req.signature)
    ok = signer.lower() == req.authorization["from"].lower()
    return {"valid": ok, "recovered": signer}


@app.post("/settle")
def settle(req: SettleRequest):
    """On-chain виконання: facilitator відправляє переказ і платить газ (WBT)."""
    signer = common.recover_signer(TOKEN_ADDRESS, CHAIN_ID, req.authorization, req.signature)
    if signer.lower() != req.authorization["from"].lower():
        return {"success": False, "error": "invalid signature"}

    w3 = get_w3()
    acct = w3.eth.account.from_key(FACILITATOR_PK)
    token = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_ADDRESS), abi=TOKEN_ABI)

    a = req.authorization
    v, r, s = _split_sig(req.signature)
    fn = token.functions.transferWithAuthorization(
        Web3.to_checksum_address(a["from"]), Web3.to_checksum_address(a["to"]),
        int(a["value"]), int(a["validAfter"]), int(a["validBefore"]),
        bytes.fromhex(a["nonce"][2:]), v, r, s,
    )
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": CHAIN_ID,
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return {
        "success": receipt.status == 1,
        "txHash": tx_hash.hex(),
        "explorer": f"https://testnet.whitechain.io/tx/0x{tx_hash.hex()}",
    }


@app.get("/")
def root():
    return {"service": "AgentPay Facilitator", "network": "whitechain-testnet", "token": TOKEN_ADDRESS}
