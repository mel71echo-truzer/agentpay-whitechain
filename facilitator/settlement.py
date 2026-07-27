import os
from web3 import Web3
from eth_account import Account

# AgentPayRouter ABI for atomic settlement
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "from", "type": "address"},
            {"internalType": "address", "name": "seller", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "uint256", "name": "validAfter", "type": "uint256"},
            {"internalType": "uint256", "name": "validBefore", "type": "uint256"},
            {"internalType": "bytes32", "name": "nonce", "type": "bytes32"},
            {"internalType": "uint8", "name": "v", "type": "uint8"},
            {"internalType": "bytes32", "name": "r", "type": "bytes32"},
            {"internalType": "bytes32", "name": "s", "type": "bytes32"}
        ],
        "name": "settlePaymentAtomic",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

class SettlementService:
    def __init__(self, w3: Web3, router_address: str, facilitator_private_key: str):
        self.w3 = w3
        self.router_address = Web3.to_checksum_address(router_address)
        self.account = Account.from_key(facilitator_private_key)
        self.contract = self.w3.eth.contract(address=self.router_address, abi=ROUTER_ABI)

    def settle_atomic_payment(self, payment_data: dict, seller_address: str) -> str:
        """
        Calls AgentPayRouter.settlePaymentAtomic to process EIP-3009 payment on-chain.
        """
        seller = Web3.to_checksum_address(seller_address)
        from_address = Web3.to_checksum_address(payment_data["from"])

        tx = self.contract.functions.settlePaymentAtomic(
            from_address,
            seller,
            int(payment_data["amount"]),
            int(payment_data["validAfter"]),
            int(payment_data["validBefore"]),
            bytes.fromhex(payment_data["nonce"].replace("0x", "")),
            int(payment_data["v"]),
            bytes.fromhex(payment_data["r"].replace("0x", "")),
            bytes.fromhex(payment_data["s"].replace("0x", ""))
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gasPrice': self.w3.eth.gas_price
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return tx_hash.hex()
