import json
import os
from pathlib import Path
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

MINIMAL_ERC20_ABI = [
    {
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_amount", "type": "uint256"}],
        "name": "faucet",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getFaucetMax",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]

class FaucetService:
    def __init__(self):
        self.rpc_url = os.getenv("RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {self.rpc_url}")

        self.private_key = os.getenv("FAUCET_PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("FAUCET_PRIVATE_KEY not found in environment")

        self.account = self.w3.eth.account.from_key(self.private_key)
        logger.info(f"Faucet wallet: {self.account.address}")

        self.contract_address = os.getenv("CONTRACT_ADDRESS")
        if not self.contract_address:
            deployed_path = Path(__file__).parent.parent / "out" / "deployed_address.json"
            if deployed_path.exists():
                with open(deployed_path) as f:
                    data = json.load(f)
                    self.contract_address = data["contract_address"]
            else:
                raise ValueError(
                    "CONTRACT_ADDRESS env var not set and no deployed_address.json found"
                )

        abi_path = Path(__file__).parent.parent / "out" / "MockUSDC.json"
        if abi_path.exists():
            with open(abi_path) as f:
                raw = json.load(f)
                self.abi = raw.get("abi", raw) if isinstance(raw, dict) else raw
            logger.info(f"ABI loaded from file: {abi_path}")
        else:
            self.abi = MINIMAL_ERC20_ABI
            logger.warning(
                "ABI file not found, using embedded minimal ABI. "
                "Make sure MINIMAL_ERC20_ABI matches your deployed contract."
            )

        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.contract_address),
            abi=self.abi
        )

        logger.info(f"Contract loaded: {self.contract_address}")

    def get_balance(self, address: str) -> float:
        try:
            balance = self.contract.functions.balanceOf(
                Web3.to_checksum_address(address)
            ).call()
            decimals = self.contract.functions.decimals().call()
            return balance / (10 ** decimals)
        except Exception as e:
            logger.error(f"Balance check failed for {address}: {e}")
            raise

    def get_eth_balance(self, address: str) -> float:
        try:
            balance_wei = self.w3.eth.get_balance(Web3.to_checksum_address(address))
            return float(self.w3.from_wei(balance_wei, 'ether'))
        except Exception as e:
            logger.error(f"ETH balance check failed for {address}: {e}")
            raise

    def send_tokens(self, to_address: str, amount: float) -> str:
        try:
            to_address = Web3.to_checksum_address(to_address)
            decimals = self.contract.functions.decimals().call()
            amount_wei = int(amount * (10 ** decimals))

            try:
                max_faucet = self.contract.functions.getFaucetMax().call()
                if amount_wei > max_faucet:
                    logger.warning(
                        f"Amount {amount_wei} exceeds max {max_faucet}, adjusting"
                    )
                    amount_wei = max_faucet
            except Exception:
                logger.warning("getFaucetMax() not available, skipping check")

            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block['baseFeePerGas']
            max_priority_fee = self.w3.to_wei(0.1, 'gwei')
            max_fee_per_gas = base_fee * 2 + max_priority_fee

            tx = self.contract.functions.faucet(
                to_address,
                amount_wei
            ).build_transaction({
                'chainId': int(os.getenv("CHAIN_ID", "421614")),
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 100000,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': max_priority_fee,
            })

            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Sent {amount} USDC to {to_address}, tx: {tx_hash.hex()}")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                raise Exception("Transaction reverted on-chain")
            return tx_hash.hex()

        except ContractLogicError as e:
            logger.error(f"Contract error sending to {to_address}: {e}")
            raise Exception(f"Contract error: {str(e)}")
        except Exception as e:
            logger.error(f"Error sending to {to_address}: {e}")
            raise

    def send_eth(self, to_address: str, amount_eth: float) -> str:
        try:
            to_address = Web3.to_checksum_address(to_address)
            amount_wei = self.w3.to_wei(amount_eth, 'ether')
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block['baseFeePerGas']
            max_priority_fee = self.w3.to_wei(0.1, 'gwei')
            max_fee_per_gas = base_fee * 2 + max_priority_fee
            tx = {
                'chainId': int(os.getenv("CHAIN_ID", "421614")),
                'from': self.account.address,
                'to': to_address,
                'value': amount_wei,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 21000,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': max_priority_fee,
            }

            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Sent {amount_eth} ETH to {to_address}, tx: {tx_hash.hex()}")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                raise Exception("ETH transaction reverted on-chain")
            return tx_hash.hex()

        except Exception as e:
            logger.error(f"Error sending ETH to {to_address}: {e}")
            raise