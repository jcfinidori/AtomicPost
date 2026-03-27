"""
solana_service.py — POSTAL SETTLEMENT POC
Solana devnet settlement demo using native SOL transfers + SPL Memo program.

No SPL token dependencies. Each transaction carries a structured JSON memo
that represents a USD-ST cross-border settlement obligation.

Wallets loaded from accounts.json["solana"]["treasury" | "operator_a"].
"""

import json
import time
import base58

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.system_program import transfer, TransferParams
from solders.transaction import Transaction
from solders.message import Message
from solders.instruction import Instruction, AccountMeta
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Confirmed
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

SOLANA_ENDPOINT = "https://api.devnet.solana.com"
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
EXPLORER_TX_URL = "https://explorer.solana.com/tx/{}?cluster=devnet"
ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"

# Symbolic transfer amount: 0.001 SOL in lamports
SETTLEMENT_LAMPORTS = 100_000


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_solana_keypair(role: str) -> Keypair:
    """
    Load a Keypair from accounts.json for the given role.

    Args:
        role: "treasury" or "operator_a"

    Returns:
        Keypair object ready for signing.

    Raises:
        FileNotFoundError: if accounts.json is missing
        KeyError: if the role is not present under accounts["solana"]
        ValueError: if private_key is empty
    """
    with open(ACCOUNTS_FILE) as f:
        accounts = json.load(f)

    entry = accounts.get("solana", {}).get(role)
    if not entry:
        raise KeyError(
            f"accounts.json is missing solana.{role}. "
            "Add public_key and private_key for this role."
        )

    private_key = entry.get("private_key", "")
    if not private_key:
        raise ValueError(f"solana.{role}.private_key is empty in accounts.json.")

    secret_bytes = base58.b58decode(private_key)
    keypair = Keypair.from_bytes(secret_bytes)
    return keypair


def get_sol_balance(public_key_str: str) -> float:
    """
    Return the SOL balance for a wallet address as a float.
    Returns 0.0 on any error.

    Args:
        public_key_str: base58 public key string
    """
    try:
        pubkey = Pubkey.from_string(public_key_str)
        client = Client(SOLANA_ENDPOINT)
        result = client.get_balance(pubkey)
        return result.value / 1_000_000_000
    except Exception:
        return 0.0


# ── Public API ─────────────────────────────────────────────────────────────────

def check_balances() -> dict:
    """
    Check SOL balance for treasury and operator_a.

    Returns:
        {
            "treasury":   float,
            "operator_a": float
        }
    """
    try:
        with open(ACCOUNTS_FILE) as f:
            accounts = json.load(f)

        solana = accounts.get("solana", {})
        treasury_pub  = solana.get("treasury",  {}).get("public_key", "")
        operator_a_pub = solana.get("operator_a", {}).get("public_key", "")

        treasury_bal  = get_sol_balance(treasury_pub)
        operator_a_bal = get_sol_balance(operator_a_pub)

        print(f"  treasury   : {treasury_bal:.6f} SOL  ({treasury_pub[:20]}...)")
        print(f"  operator_a : {operator_a_bal:.6f} SOL  ({operator_a_pub[:20]}...)")

        return {
            "treasury":   treasury_bal,
            "operator_a": operator_a_bal,
        }

    except Exception as exc:
        print(f"  check_balances error: {exc}")
        return {
            "treasury":   0.0,
            "operator_a": 0.0,
            "error":      str(exc),
        }


def simple_settlement_demo(
    clearing_cycle_id: str,
    amount_usd: float,
    transaction_type: str = "CrossBorderSettlement",
) -> dict:
    """
    Execute a single Solana devnet transaction representing a USD-ST
    cross-border settlement.

    Sends 0.001 SOL (symbolic) from treasury to operator_a with a
    structured JSON memo via the SPL Memo program.

    Args:
        clearing_cycle_id:  e.g. "CYCLE_2026-03-20"
        amount_usd:         nominal USD-ST amount recorded in the memo
        transaction_type:   label embedded in the memo (default "CrossBorderSettlement")

    Returns:
        {
            "success": bool,
            "tx_signature": str | None,
            "explorer_url": str | None,
            "execution_seconds": float | None,
            "clearing_cycle_id": str,
            "amount_usd": float,
            "memo": dict | None,
            "network": "Solana Devnet",
            "error": str | None
        }
    """
    try:
        # Step 1: Load keypairs
        treasury_keypair = load_solana_keypair("treasury")

        with open(ACCOUNTS_FILE) as f:
            accounts = json.load(f)
        operator_a_public_key = accounts["solana"]["operator_a"]["public_key"]

        # Step 2: Get recent blockhash
        client = Client(SOLANA_ENDPOINT)
        blockhash_resp = client.get_latest_blockhash()
        recent_blockhash = blockhash_resp.value.blockhash

        # Step 3: Build memo content
        memo_content = json.dumps({
            "SettlementCycleID": clearing_cycle_id,
            "Amount_USD":        str(amount_usd),
            "Token":             "USD-ST",
            "Protocol":          "PostalSettle",
            "Network":           "Solana Devnet",
            "From":              "La Poste CI Treasury",
            "To":                "UAE Post Operator",
            "Type":              transaction_type,
        })
        memo_bytes = memo_content.encode("utf-8")

        # Step 4: Build memo instruction
        memo_program_pubkey = Pubkey.from_string(MEMO_PROGRAM_ID)
        memo_instruction = Instruction(
            program_id=memo_program_pubkey,
            accounts=[],
            data=memo_bytes,
        )

        # Step 5: Build transfer instruction
        from_pubkey = treasury_keypair.pubkey()
        to_pubkey   = Pubkey.from_string(operator_a_public_key)
        transfer_instruction = transfer(
            TransferParams(
                from_pubkey=from_pubkey,
                to_pubkey=to_pubkey,
                lamports=SETTLEMENT_LAMPORTS,
            )
        )

        # Step 6: Build and sign transaction
        message = Message.new_with_blockhash(
            [memo_instruction, transfer_instruction],
            from_pubkey,
            recent_blockhash,
        )
        transaction = Transaction.new_unsigned(message)
        transaction.sign([treasury_keypair], recent_blockhash)

        # Step 7: Send transaction
        start_time = time.time()
        opts = TxOpts(skip_preflight=False)
        result = client.send_transaction(transaction, opts)
        signature = str(result.value)

        # Step 8: Wait for confirmation
        max_attempts = 30
        for attempt in range(max_attempts):
            time.sleep(2)
            try:
                status = client.get_signature_statuses([result.value])
                if status.value[0] is not None:
                    conf = str(status.value[0].confirmation_status)
                    if "confirmed" in conf.lower() or "finalized" in conf.lower():
                        execution_seconds = time.time() - start_time
                        explorer_url = EXPLORER_TX_URL.format(signature)
                        print(f"Transaction confirmed!")
                        print(f"Signature: {signature}")
                        print(f"Explorer: {explorer_url}")
                        print(f"Time: {execution_seconds:.2f}s")
                        return {
                            "success":           True,
                            "tx_signature":      signature,
                            "explorer_url":      explorer_url,
                            "execution_seconds": round(execution_seconds, 2),
                            "clearing_cycle_id": clearing_cycle_id,
                            "amount_usd":        amount_usd,
                            "memo":              json.loads(memo_content),
                            "network":           "Solana Devnet",
                            "error":             None,
                        }
            except Exception:
                pass
            print(f"Waiting... attempt {attempt + 1}/30")

        return {
            "success":           False,
            "tx_signature":      signature,
            "explorer_url":      EXPLORER_TX_URL.format(signature),
            "execution_seconds": None,
            "clearing_cycle_id": clearing_cycle_id,
            "amount_usd":        amount_usd,
            "memo":              None,
            "network":           "Solana Devnet",
            "error":             "Confirmation timeout",
        }

    except Exception as exc:
        print(f"  simple_settlement_demo error: {exc}")
        return {
            "success":           False,
            "tx_signature":      None,
            "explorer_url":      None,
            "execution_seconds": None,
            "clearing_cycle_id": clearing_cycle_id,
            "amount_usd":        amount_usd,
            "memo":              None,
            "network":           "Solana Devnet",
            "error":             str(exc),
        }


def run_full_demo(clearing_cycle_id: str, amount_usd: float) -> dict:
    """
    Run three settlement transactions in sequence to simulate a full
    USD-ST clearing cycle on Solana devnet.

        Step 1 — Issuance:    treasury mints USD-ST to operator_a
        Step 2 — Settlement:  operator_a settles cross-border obligation
        Step 3 — Redemption:  operator redeems USD-ST back to treasury

    Each step is a real on-chain transaction with a structured memo.

    Args:
        clearing_cycle_id: e.g. "CYCLE_2026-03-20"
        amount_usd:        nominal USD-ST amount for the cycle

    Returns:
        {
            "cycle_id":           str,
            "amount_usd":         float,
            "transactions":       list of step dicts,
            "total_time_seconds": float,
            "all_confirmed":      bool,
            "network":            "Solana Devnet"
        }
    """
    print(f"\n{'=' * 60}")
    print(f"  FULL DEMO CYCLE: {clearing_cycle_id}")
    print(f"  Amount: {amount_usd} USD-ST  |  Network: Solana Devnet")
    print(f"{'=' * 60}\n")

    steps_config = [
        (1, "Issuance",    "USD-ST Issuance"),
        (2, "Settlement",  "Cross-Border Settlement"),
        (3, "Redemption",  "Token Redemption"),
    ]

    transactions = []
    demo_start = time.time()
    all_confirmed = True

    for step_num, tx_type, label in steps_config:
        print(f"[Step {step_num}] {label}...")
        result = simple_settlement_demo(
            clearing_cycle_id=clearing_cycle_id,
            amount_usd=amount_usd,
            transaction_type=tx_type,
        )

        transactions.append({
            "step":              step_num,
            "type":              tx_type,
            "label":             label,
            "success":           result["success"],
            "tx_signature":      result.get("tx_signature"),
            "explorer_url":      result.get("explorer_url"),
            "execution_seconds": result.get("execution_seconds"),
            "error":             result.get("error"),
        })

        if not result["success"]:
            all_confirmed = False
            print(f"  Step {step_num} FAILED: {result.get('error')}")

    total_time = round(time.time() - demo_start, 2)

    print(f"\n{'=' * 60}")
    print(f"  Cycle complete in {total_time}s — "
          f"{'ALL CONFIRMED' if all_confirmed else 'SOME STEPS FAILED'}")
    print(f"{'=' * 60}\n")

    return {
        "cycle_id":           clearing_cycle_id,
        "amount_usd":         amount_usd,
        "transactions":       transactions,
        "total_time_seconds": total_time,
        "all_confirmed":      all_confirmed,
        "network":            "Solana Devnet",
    }


# ── Standalone entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Checking balances...")
    balances = check_balances()
    print(balances)

    print("Running simple demo...")
    result = simple_settlement_demo("CYCLE_2026-03-20", 15300)
    print(json.dumps(result, indent=2))
