import json
import os
from pathlib import Path
from typing import Dict
from web3 import Web3
from web3.exceptions import ContractLogicError
import logging

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

def _load_abi() -> list:
    """Intenta cargar el ABI desde archivo; cae al ABI mínimo embebido."""
    abi_path = Path(__file__).parent.parent / "out" / "MockUSDC.json"
    if abi_path.exists():
        with open(abi_path) as f:
            raw = json.load(f)
            abi = raw.get("abi", raw) if isinstance(raw, dict) else raw
        logger.info(f"ABI cargado desde {abi_path}")
        return abi
    logger.warning("ABI file no encontrado — usando ABI mínimo embebido")
    return MINIMAL_ERC20_ABI

class NetworkClient:
    """Conexión a una red específica: w3 + contrato + wallet."""

    def __init__(self, network_key: str, network_cfg: dict, private_key: str, abi: list):
        self.key          = network_key
        self.name         = network_cfg["name"]
        self.chain_id     = network_cfg["chain_id"]
        self.explorer_url = network_cfg["explorer_url"]
        self.eth_amount   = network_cfg["eth_amount"]
        self.w3 = Web3(Web3.HTTPProvider(network_cfg["rpc_url"]))
        if not self.w3.is_connected():
            raise ConnectionError(f"No se pudo conectar al RPC de {self.name}: {network_cfg['rpc_url']}")

        self.account = self.w3.eth.account.from_key(private_key)
        self._private_key = private_key
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(network_cfg["contract_address"]),
            abi=abi,
        )
        logger.info(
            f"[{self.name}] conectado | contrato: {network_cfg['contract_address']} "
            f"| wallet: {self.account.address}"
        )

    # ── Lecturas ──────────────────────────────────────────────────────────────

    def get_usdc_balance(self, address: str) -> float:
        balance  = self.contract.functions.balanceOf(Web3.to_checksum_address(address)).call()
        decimals = self.contract.functions.decimals().call()
        return balance / (10 ** decimals)

    def get_eth_balance(self, address: str) -> float:
        balance_wei = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self.w3.from_wei(balance_wei, "ether"))

    # ── Transacciones ─────────────────────────────────────────────────────────

    def send_tokens(self, to_address: str, amount: float) -> str:
        to_address = Web3.to_checksum_address(to_address)
        decimals   = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        try:
            max_faucet = self.contract.functions.getFaucetMax().call()
            if amount_wei > max_faucet:
                logger.warning(f"[{self.name}] amount {amount_wei} > max {max_faucet}, ajustando")
                amount_wei = max_faucet
        except Exception:
            logger.warning(f"[{self.name}] getFaucetMax() no disponible, omitiendo check")
        latest_block    = self.w3.eth.get_block("latest")
        base_fee        = latest_block["baseFeePerGas"]
        max_priority    = self.w3.to_wei(0.1, "gwei")
        max_fee_per_gas = base_fee * 2 + max_priority
        tx = self.contract.functions.faucet(to_address, amount_wei).build_transaction({
            "chainId":              self.chain_id,
            "from":                 self.account.address,
            "nonce":                self.w3.eth.get_transaction_count(self.account.address),
            "gas":                  100_000,
            "maxFeePerGas":         max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority,
        })

        signed   = self.w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash  = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt  = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status != 1:
            raise Exception(f"[{self.name}] Transacción USDC revertida on-chain")
        logger.info(f"[{self.name}] USDC enviado a {to_address} | tx: {tx_hash.hex()}")
        return tx_hash.hex()

    def send_eth(self, to_address: str, amount_eth: float) -> str:
        to_address  = Web3.to_checksum_address(to_address)
        amount_wei  = self.w3.to_wei(amount_eth, "ether")
        faucet_bal = self.w3.eth.get_balance(self.account.address)
        required   = amount_wei + self.w3.to_wei(0.001, "ether")
        if faucet_bal < required:
            raise Exception(
                f"[{self.name}] ETH insuficiente en faucet: "
                f"{self.w3.from_wei(faucet_bal, 'ether'):.6f} disponible, "
                f"se necesita {amount_eth} + gas"
            )

        latest_block    = self.w3.eth.get_block("latest")
        base_fee        = latest_block["baseFeePerGas"]
        max_priority    = self.w3.to_wei(0.1, "gwei")
        max_fee_per_gas = base_fee * 2 + max_priority
        nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")

        tx = {
            "chainId":              self.chain_id,
            "from":                 self.account.address,
            "to":                   to_address,
            "value":                amount_wei,
            "nonce":                nonce,
            "gas":                  21_000,
            "maxFeePerGas":         max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority,
        }

        signed  = self.w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status != 1:
            raise Exception(f"[{self.name}] Transacción ETH revertida on-chain")
        logger.info(f"[{self.name}] ETH enviado a {to_address} | tx: {tx_hash.hex()}")
        return tx_hash.hex()


class FaucetService:
    """
    Servicio multi-red. Mantiene un NetworkClient por cada chain configurada.
    El frontend selecciona la red pasando `network` en el body del POST /faucet.
    """

    def __init__(self):
        from .config import settings, NETWORK_CONFIGS
        if not settings.FAUCET_PRIVATE_KEY:
            raise ValueError("FAUCET_PRIVATE_KEY no encontrada en el entorno")
        self._abi      = _load_abi()
        self._clients: Dict[str, NetworkClient] = {}
        for network_key in NETWORK_CONFIGS:
            try:
                cfg    = settings.get_network_config(network_key)
                client = NetworkClient(
                    network_key  = network_key,
                    network_cfg  = cfg,
                    private_key  = settings.FAUCET_PRIVATE_KEY,
                    abi          = self._abi,
                )
                self._clients[network_key] = client
            except Exception as e:
                logger.warning(f"Red '{network_key}' deshabilitada: {e}")

        if not self._clients:
            raise RuntimeError("Ninguna red pudo inicializarse — verificar variables de entorno")

        logger.info(f"FaucetService listo | redes activas: {list(self._clients)}")

    def get_client(self, network: str) -> NetworkClient:
        client = self._clients.get(network)
        if not client:
            available = list(self._clients)
            raise ValueError(f"Red '{network}' no disponible. Disponibles: {available}")
        return client

    @property
    def active_networks(self) -> list:
        return list(self._clients.keys())

    def get_balance(self, address: str, network: str) -> float:
        return self.get_client(network).get_usdc_balance(address)

    def get_eth_balance(self, address: str, network: str) -> float:
        return self.get_client(network).get_eth_balance(address)

    def send_tokens(self, to_address: str, amount: float, network: str) -> str:
        return self.get_client(network).send_tokens(to_address, amount)

    def send_eth(self, to_address: str, amount_eth: float, network: str) -> str:
        return self.get_client(network).send_eth(to_address, amount_eth)