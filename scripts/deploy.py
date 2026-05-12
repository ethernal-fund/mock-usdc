import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_abi import encode

sys.path.append(str(Path(__file__).parent.parent))
load_dotenv()

CONTRACT_NAME = "MockUSDC"

NETWORKS = {
    # Testnets
    "sepolia": {
        "chain_id": 11155111,
        "rpc": os.getenv("SEPOLIA_RPC_URL"),
        "explorer": "https://sepolia.etherscan.io",
        "folder": "deployments/ethereum/sepolia",
        "is_mainnet": False,
    },
    "arbitrum-sepolia": {
        "chain_id": 421614,
        "rpc": os.getenv("ARBITRUM_SEPOLIA_RPC_URL"),
        "explorer": "https://sepolia.arbiscan.io",
        "folder": "deployments/arbitrum/sepolia",
        "is_mainnet": False,
    },
    "polygon-amoy": {
        "chain_id": 80002,
        "rpc": os.getenv("POLYGON_AMOY_RPC_URL"),
        "explorer": "https://amoy.polygonscan.com",
        "folder": "deployments/polygon/amoy",
        "is_mainnet": False,
    },
    # Mainnets
    "ethereum": {
        "chain_id": 1,
        "rpc": os.getenv("ETHEREUM_RPC_URL"),
        "explorer": "https://etherscan.io",
        "folder": "deployments/ethereum/mainnet",
        "is_mainnet": True,
    },
    "arbitrum": {
        "chain_id": 42161,
        "rpc": os.getenv("ARBITRUM_RPC_URL"),
        "explorer": "https://arbiscan.io",
        "folder": "deployments/arbitrum/mainnet",
        "is_mainnet": True,
    },
    "polygon": {
        "chain_id": 137,
        "rpc": os.getenv("POLYGON_RPC_URL"),
        "explorer": "https://polygonscan.com",
        "folder": "deployments/polygon/mainnet",
        "is_mainnet": True,
    },
}

CONSTRUCTOR_ARGS = {
    "_name": "Mock USD Coin",
    "_symbol": "USDC",
    "_decimals": 6,
}


def get_encoded_args() -> str:
    encoded = encode(
        ["string", "string", "uint8"],
        [
            CONSTRUCTOR_ARGS["_name"],
            CONSTRUCTOR_ARGS["_symbol"],
            CONSTRUCTOR_ARGS["_decimals"],
        ]
    )
    return encoded.hex()


def deploy_mock_usdc(network: str):
    config = NETWORKS.get(network)
    if not config:
        raise ValueError(f"Red no soportada: {network}")

    if config["is_mainnet"]:
        confirm = input(f"\n⚠️  Vas a deployar en MAINNET ({network}). ¿Estás seguro? [s/N]: ")
        if confirm.lower() != "s":
            print("Deploy cancelado.")
            sys.exit(0)

    RPC_URL = config["rpc"]
    if not RPC_URL:
        raise ValueError(f"RPC_URL no configurada para {network} en .env")

    PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
    if not PRIVATE_KEY:
        raise ValueError("DEPLOYER_PRIVATE_KEY no configurada")

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)

    print(f"\n🚀 Desplegando {CONTRACT_NAME} en {network.upper()}")
    print(f"📍 Deployer: {account.address}")
    print(f"💰 Balance:  {w3.from_wei(w3.eth.get_balance(account.address), 'ether'):.4f} ETH")

    # Compilación
    script_dir = Path(__file__).parent
    contract_path = script_dir.parent / "contracts" / "mock-usdc.vy"
    source = contract_path.read_text()

    import vyper
    compiled = vyper.compile_code(source, output_formats=["abi", "bytecode"])
    abi, bytecode = compiled["abi"], compiled["bytecode"]

    factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    # Gas por red
    if network in ("polygon", "polygon-amoy"):
        max_priority = w3.to_wei(30, "gwei")
        max_fee      = w3.to_wei(100, "gwei")
    elif network in ("ethereum", "sepolia"):
        max_priority = w3.to_wei(0.1, "gwei")
        max_fee      = w3.to_wei(2, "gwei")
    else:  # arbitrum
        max_priority = w3.to_wei(0.01, "gwei")
        max_fee      = w3.to_wei(0.1, "gwei")

    tx = factory.constructor(
        CONSTRUCTOR_ARGS["_name"],
        CONSTRUCTOR_ARGS["_symbol"],
        CONSTRUCTOR_ARGS["_decimals"]
    ).build_transaction({
        "chainId": config["chain_id"],
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 3_500_000,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority,
    })

    print(f"📤 Enviando transacción...")
    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

    if receipt.status != 1:
        raise Exception("❌ Deploy fallido")

    addr     = receipt["contractAddress"]
    encoded  = get_encoded_args()
    ts       = datetime.now(timezone.utc).isoformat()

    print(f"✅ ¡ÉXITO! MockUSDC desplegado")
    print(f"📋 Address:  {addr}")
    print(f"🔗 Explorer: {config['explorer']}/address/{addr}")

    # Crear carpeta de deployments
    script_dir = Path(__file__).parent
    output_dir = script_dir.parent / config["folder"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON con datos completos del deploy
    deploy_data = {
        "contract": CONTRACT_NAME,
        "address": addr,
        "network": network,
        "chain_id": config["chain_id"],
        "is_mainnet": config["is_mainnet"],
        "deployer": account.address,
        "tx_hash": tx_hash.hex(),
        "block": receipt["blockNumber"],
        "gas_used": receipt["gasUsed"],
        "explorer": f"{config['explorer']}/address/{addr}",
        "constructor_args": CONSTRUCTOR_ARGS,
        "constructor_args_encoded": encoded,
        "timestamp": ts,
    }

    json_path = output_dir / f"{CONTRACT_NAME}.json"
    json_path.write_text(json.dumps(deploy_data, indent=2))
    print(f"📄 Deploy info: {json_path}")

    txt_path = output_dir / f"{CONTRACT_NAME}_constructor_args.txt"
    txt_path.write_text(encoded)
    print(f"📄 Encoded args: {txt_path}")

    return deploy_data


if __name__ == "__main__":
    valid_networks = list(NETWORKS.keys())

    if len(sys.argv) < 2:
        print(f"Uso: python deploy_mock_usdc.py <red>")
        print(f"\nTestnets:  {', '.join(k for k, v in NETWORKS.items() if not v['is_mainnet'])}")
        print(f"Mainnets:  {', '.join(k for k, v in NETWORKS.items() if v['is_mainnet'])}")
        sys.exit(1)

    network = sys.argv[1]
    if network not in valid_networks:
        print(f"❌ Red inválida: '{network}'")
        print(f"   Redes disponibles: {', '.join(valid_networks)}")
        sys.exit(1)

    try:
        deploy_mock_usdc(network)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)