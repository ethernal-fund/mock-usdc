import json
import os
from web3 import Web3
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class MockUSDCFaucet:
    def __init__(self, contract_address=None):
        self.w3 = Web3(Web3.HTTPProvider(
            "https://sepolia-rollup.arbitrum.io/rpc"
        ))
        self.private_key = os.getenv("FAUCET_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("Necesitas configurar FAUCET_PRIVATE_KEY o DEPLOYER_PRIVATE_KEY en .env")
        self.account = self.w3.eth.account.from_key(self.private_key)
        print(f"💰 Usando wallet: {self.account.address}")
        abi_path = Path(__file__).parent.parent / "out" / "MockUSDC.json"
        with open(abi_path, "r") as f:
            self.abi = json.load(f)

        self.contract_address = contract_address or self.load_deployed_address()
        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=self.abi
        )
        print(f"📄 Contrato: {self.contract_address}")

    def load_deployed_address(self):
        deployed_path = Path(__file__).parent.parent / "out" / "deployed_address.json"
        with open(deployed_path, "r") as f:
            data = json.load(f)
        return data["contract_address"]

    def get_balance(self, address):
        balance = self.contract.functions.balanceOf(address).call()
        decimals = self.contract.functions.decimals().call()
        return balance / (10 ** decimals)

    def send_tokens(self, to_address, amount=100):
        decimals = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        latest_block = self.w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = self.w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        tx = self.contract.functions.transfer(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"✅ Enviados {amount} USDC a {to_address}")
        print(f"🔄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            new_balance = self.get_balance(to_address)
            print(f"✅ Confirmado! Nuevo balance: {new_balance:,.2f} USDC")
        else:
            print(f"❌ Transacción falló")
        return tx_hash.hex()

    def mint_tokens(self, to_address, amount=100):
        decimals = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        latest_block = self.w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = self.w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        tx = self.contract.functions.mint(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"✅ Minteados {amount:,.0f} USDC a {to_address}")
        print(f"🔄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            new_balance = self.get_balance(to_address)
            print(f"✅ Confirmado! Nuevo balance: {new_balance:,.2f} USDC")
        else:
            print(f"❌ Transacción falló")
        return tx_hash.hex()

    def faucet_tokens(self, to_address, amount=10000):
        decimals = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        max_faucet = self.contract.functions.getFaucetMax().call()
        if amount_wei > max_faucet:
            print(f"⚠️  Máximo permitido: {max_faucet / 10**decimals:,.0f} USDC")
            print(f"⚠️  Ajustando cantidad al máximo permitido...")
            amount_wei = max_faucet
            amount = max_faucet / 10**decimals
        latest_block = self.w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = self.w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        tx = self.contract.functions.faucet(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"✅ Faucet enviado: {amount:,.0f} USDC a {to_address}")
        print(f"🔄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            new_balance = self.get_balance(to_address)
            print(f"✅ Confirmado! Nuevo balance: {new_balance:,.2f} USDC")
        else:
            print(f"❌ Transacción falló")
        return tx_hash.hex()

    def check_owner(self):
        try:
            contract_owner = self.contract.functions.getOwner().call()
            is_owner = contract_owner.lower() == self.account.address.lower()
            print(f"\n🔍 Owner del contrato: {contract_owner}")
            print(f"🔍 Tu wallet:          {self.account.address}")
            print(f"{'✅ Eres owner!' if is_owner else '❌ NO eres owner'}\n")
            return is_owner
        except Exception as e:
            print(f"⚠️  No se pudo verificar owner: {e}")
            return False

if __name__ == "__main__":
    print("=" * 70)
    print("🎁 FONDEAR FAUCET API - ADMIN SCRIPT")
    print("=" * 70)

    faucet = MockUSDCFaucet()

    FAUCET_API_ADDRESS = "0xFc1bA574c1622A1b116dFeFEE2215F3F53bB2c51"

    is_owner = faucet.check_owner()
    if not is_owner:
        print("❌ ERROR: No eres el owner del contrato")
        exit()

    print(f"💰 Balance actual: {faucet.get_balance(FAUCET_API_ADDRESS):,.2f} USDC\n")

    AMOUNT = 100_000_000  

    print(f"🎯 Minteando {AMOUNT:,.0f} USDC a {FAUCET_API_ADDRESS}\n")

    try:
        faucet.mint_tokens(FAUCET_API_ADDRESS, AMOUNT)
        print(f"\n✅ Balance final: {faucet.get_balance(FAUCET_API_ADDRESS):,.2f} USDC")
        print(f"🔗 https://sepolia.arbiscan.io/address/{FAUCET_API_ADDRESS}")
    except Exception as e:
        print(f"❌ ERROR: {e}")