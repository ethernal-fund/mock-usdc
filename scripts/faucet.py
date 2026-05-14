"""
fund_faucet.py — Fondea la wallet faucet en múltiples redes de una sola vez.

Uso:
    python scripts/fund_faucet.py                        # fondea todas las redes configuradas
    python scripts/fund_faucet.py sepolia                # solo Sepolia
    python scripts/fund_faucet.py arbitrum-sepolia       # solo Arb Sepolia
"""

import json
import os
import sys
from pathlib import Path
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# ── Config de redes (mismo dict que deploy.py) ────────────────────────────────
NETWORKS = {
    "sepolia": {
        "chain_id":    11155111,
        "rpc_env":     "SEPOLIA_RPC_URL",
        "explorer":    "https://sepolia.etherscan.io",
        "deployment":  "deployments/ethereum/sepolia/MockUSDC.json",
    },
    "arbitrum-sepolia": {
        "chain_id":    421614,
        "rpc_env":     "ARBITRUM_SEPOLIA_RPC_URL",
        "explorer":    "https://sepolia.arbiscan.io",
        "deployment":  "deployments/arbitrum/sepolia/MockUSDC.json",
    },
}

# ── Dirección de la wallet faucet (la que usa la API) ─────────────────────────
FAUCET_API_ADDRESS = os.getenv("FAUCET_ADDRESS", "0xFc1bA574c1622A1b116dFeFEE2215F3F53bB2c51")

# ── Cuántos USDC mintear por red ──────────────────────────────────────────────
MINT_AMOUNT = 100_000_000  # 100M USDC


def load_deployment(network_key: str) -> dict:
    """Lee el JSON de deployment para obtener la address del contrato."""
    root = Path(__file__).parent.parent
    path = root / NETWORKS[network_key]["deployment"]
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró deployment en {path}\n"
            f"  → Primero ejecutá: python scripts/deploy.py {network_key}"
        )
    with open(path) as f:
        return json.load(f)


def load_abi() -> list:
    """Carga el ABI desde out/MockUSDC.json o usa el mínimo embebido."""
    abi_path = Path(__file__).parent.parent / "out" / "MockUSDC.json"
    if abi_path.exists():
        with open(abi_path) as f:
            raw = json.load(f)
            return raw.get("abi", raw) if isinstance(raw, dict) else raw

    # ABI mínimo con las funciones que necesitamos
    return [
        {"inputs": [{"name": "_to", "type": "address"}, {"name": "_amount", "type": "uint256"}],
         "name": "mint", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [{"name": "_owner", "type": "address"}],
         "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
         "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "decimals",
         "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "getOwner",
         "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    ]


def fund_network(network_key: str, private_key: str, abi: list) -> bool:
    """Fondea la wallet faucet en una red. Devuelve True si tuvo éxito."""
    cfg        = NETWORKS[network_key]
    rpc_url    = os.getenv(cfg["rpc_env"])
    chain_id   = cfg["chain_id"]
    explorer   = cfg["explorer"]

    if not rpc_url:
        print(f"  ⚠️  {cfg['rpc_env']} no configurada — saltando {network_key}")
        return False

    print(f"\n{'─'*60}")
    print(f"  Red: {network_key.upper()}")
    print(f"{'─'*60}")

    # Conexión
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"  ❌ No se pudo conectar al RPC: {rpc_url}")
        return False

    account = w3.eth.account.from_key(private_key)
    eth_bal = w3.from_wei(w3.eth.get_balance(account.address), "ether")
    print(f"  Deployer: {account.address}")
    print(f"  ETH bal:  {eth_bal:.4f} ETH")

    # Contrato
    try:
        deployment       = load_deployment(network_key)
        contract_address = deployment["address"]
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        return False

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi,
    )
    print(f"  Contrato: {contract_address}")

    # Verificar owner
    try:
        owner    = contract.functions.getOwner().call()
        is_owner = owner.lower() == account.address.lower()
        print(f"  Owner:    {owner}")
        if not is_owner:
            print(f"  ❌ Tu wallet no es owner — no podés mintear en esta red")
            return False
        print(f"  ✅ Sos owner")
    except Exception as e:
        print(f"  ⚠️  No se pudo verificar owner: {e}")

    # Balance actual
    decimals     = contract.functions.decimals().call()
    bal_before   = contract.functions.balanceOf(
        Web3.to_checksum_address(FAUCET_API_ADDRESS)
    ).call() / (10 ** decimals)
    print(f"\n  Faucet API address: {FAUCET_API_ADDRESS}")
    print(f"  Balance actual:     {bal_before:,.0f} USDC")
    print(f"  A mintear:          {MINT_AMOUNT:,.0f} USDC")

    # Tx
    amount_wei = int(MINT_AMOUNT * (10 ** decimals))

    latest_block    = w3.eth.get_block("latest")
    base_fee        = latest_block["baseFeePerGas"]
    max_priority    = w3.to_wei(0.1, "gwei")
    max_fee_per_gas = base_fee * 2 + max_priority

    tx = contract.functions.mint(
        Web3.to_checksum_address(FAUCET_API_ADDRESS),
        amount_wei,
    ).build_transaction({
        "chainId":              chain_id,
        "from":                 account.address,
        "nonce":                w3.eth.get_transaction_count(account.address),
        "gas":                  100_000,
        "maxFeePerGas":         max_fee_per_gas,
        "maxPriorityFeePerGas": max_priority,
    })

    signed  = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\n  📤 Tx enviada: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        print(f"  ❌ Transacción revertida on-chain")
        return False

    bal_after = contract.functions.balanceOf(
        Web3.to_checksum_address(FAUCET_API_ADDRESS)
    ).call() / (10 ** decimals)

    print(f"  ✅ Confirmado!")
    print(f"  Balance nuevo: {bal_after:,.0f} USDC")
    print(f"  🔗 {explorer}/tx/{tx_hash.hex()}")
    return True


def main():
    private_key = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("FAUCET_PRIVATE_KEY")
    if not private_key:
        print("❌ Configurá DEPLOYER_PRIVATE_KEY o FAUCET_PRIVATE_KEY en .env")
        sys.exit(1)

    # Si se pasa una red como argumento, solo fondear esa
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target not in NETWORKS:
            print(f"❌ Red inválida: '{target}'. Opciones: {list(NETWORKS)}")
            sys.exit(1)
        networks_to_fund = [target]
    else:
        networks_to_fund = list(NETWORKS.keys())

    print("=" * 60)
    print("  FONDEAR WALLET FAUCET")
    print(f"  Redes: {', '.join(networks_to_fund)}")
    print(f"  Destino: {FAUCET_API_ADDRESS}")
    print(f"  Monto: {MINT_AMOUNT:,.0f} USDC por red")
    print("=" * 60)

    abi     = load_abi()
    results = {}

    for net in networks_to_fund:
        try:
            results[net] = fund_network(net, private_key, abi)
        except Exception as e:
            print(f"\n  ❌ Error inesperado en {net}: {e}")
            results[net] = False

    # Resumen final
    print(f"\n{'='*60}")
    print("  RESUMEN")
    print(f"{'='*60}")
    for net, ok in results.items():
        status = "✅ OK" if ok else "❌ FALLÓ"
        print(f"  {net:<25} {status}")


if __name__ == "__main__":
    main()