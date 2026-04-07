import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_abi import encode  

sys.path.append(str(Path(__file__).parent.parent))
load_dotenv()

CONTRACT_NAME = "MockUSDC"
CHAIN_ID = 421614

CONSTRUCTOR_ARGS = {
    "_name": "USD Coin",
    "_symbol": "USDC",
    "_decimals": 6,
}

def save_artifacts(out_dir: Path, contract_address, abi, tx_hash, deployer, block_number, timestamp):
    out_dir.mkdir(parents=True, exist_ok=True)

    abi_path = out_dir / f"{CONTRACT_NAME}.json"
    with open(abi_path, "w") as f:
        json.dump(abi, f, indent=2)
    print(f"📄 ABI → {abi_path}")
    encoded = encode(
        ["string", "string", "uint8"],
        [CONSTRUCTOR_ARGS["_name"], CONSTRUCTOR_ARGS["_symbol"], CONSTRUCTOR_ARGS["_decimals"]],
    )
    encoding_hex = encoded.hex()
    encoding_path = out_dir / f"{CONTRACT_NAME}.encoding.txt"
    encoding_path.write_text(encoding_hex)
    print(f"🔐 Constructor args encoded → {encoding_path}")

    deploy_info = {
        "network": "arbitrum-sepolia",
        "chain_id": CHAIN_ID,
        "contract_name": CONTRACT_NAME,
        "contract_address": contract_address,
        "deployer": deployer,
        "tx_hash": tx_hash,
        "constructor_args": CONSTRUCTOR_ARGS,
        "constructor_args_encoded": encoding_hex,
        "block_number": block_number,
        "timestamp": timestamp,
        "arbiscan": f"https://sepolia.arbiscan.io/address/{contract_address}",
    }
    with open(out_dir / "deployed_address.json", "w") as f:
        json.dump(deploy_info, f, indent=2)

    return encoding_hex

def deploy_mock_usdc():
    RPC_URL = os.getenv("RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
    PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
    if not PRIVATE_KEY:
        raise ValueError("DEPLOYER_PRIVATE_KEY no configurada en .env")

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"🚀 Desplegando {CONTRACT_NAME} desde: {account.address}")

    script_dir = Path(__file__).parent              # mock-usdc/scripts/
    contract_path = script_dir.parent / "contracts" / "mock-usdc.vy"
    out_dir = script_dir.parent / "out"             # mock-usdc/out/
    source = contract_path.read_text()
    print("🔨 Compilando contrato Vyper...")
    import vyper
    compiled = vyper.compile_code(source, output_formats=["abi", "bytecode"])
    abi, bytecode = compiled["abi"], compiled["bytecode"]
    print("✅ Compilado")
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    try:
        gas_estimate = factory.constructor(
            CONSTRUCTOR_ARGS["_name"], CONSTRUCTOR_ARGS["_symbol"], CONSTRUCTOR_ARGS["_decimals"]
        ).estimate_gas({"from": account.address})
    except Exception:
        gas_estimate = 2_000_000

    latest = w3.eth.get_block("latest")
    base_fee = latest["baseFeePerGas"]
    max_priority = w3.to_wei(0.1, "gwei")
    max_fee = base_fee * 2 + max_priority
    tx = factory.constructor(
        CONSTRUCTOR_ARGS["_name"], CONSTRUCTOR_ARGS["_symbol"], CONSTRUCTOR_ARGS["_decimals"]
    ).build_transaction({
        "chainId": CHAIN_ID,
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": int(gas_estimate * 1.5),
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority,
        "value": 0,
    })

    print("📤 Enviando deploy...")
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status != 1:
        raise Exception("Deploy fallido")

    addr = receipt["contractAddress"]
    block = receipt["blockNumber"]
    ts = w3.eth.get_block(block)["timestamp"]

    print(f"\n✅ Contrato: {addr}")
    print(f"🔗 https://sepolia.arbiscan.io/address/{addr}")

    encoding_hex = save_artifacts(out_dir, addr, abi, tx_hash.hex(), account.address, block, ts)

    inst = w3.eth.contract(address=addr, abi=abi)
    decimals = inst.functions.decimals().call()
    print(f"\n✅ {inst.functions.name().call()} ({inst.functions.symbol().call()})")
    print(f"✅ Owner: {inst.functions.getOwner().call()}")
    print(f"✅ Faucet Max: {inst.functions.getFaucetMax().call() / 10**decimals:,.0f} USDC")
    print("\n" + "=" * 55)
    print("📋 VERIFICAR EN ARBISCAN:")
    print(f"  URL: https://sepolia.arbiscan.io/address/{addr}#code")
    print("  Compiler: Vyper (Single file)")
    print("  Version: v0.4.0 (o la que usaste)")
    print("  Constructor Arguments ABI-encoded:")
    print(f"  {encoding_hex}")
    print("=" * 55)

    return addr, abi

if __name__ == "__main__":
    try:
        deploy_mock_usdc()
    except Exception as e:
        import traceback; traceback.print_exc()
        sys.exit(1)