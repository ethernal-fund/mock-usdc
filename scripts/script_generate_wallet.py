from web3 import Web3

w3 = Web3()
account = w3.eth.account.create()

print("=" * 60)
print("🔑 NUEVA WALLET PARA FAUCET")
print("=" * 60)
print(f"Address: {account.address}")
print(f"Private Key: {account.key.hex()}")
print("=" * 60)
print("⚠️  GUARDA LA PRIVATE KEY DE FORMA SEGURA")
print("⚠️  NO LA COMPARTAS CON NADIE")
print("=" * 60)