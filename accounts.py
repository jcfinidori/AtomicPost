"""
accounts.py — loads wallet credentials from accounts.json

Usage in other scripts:
    from accounts import get_wallet
    issuer = get_wallet("issuer_treasury")
    print(issuer.address)
"""

import json
from pathlib import Path
from xrpl.wallet import Wallet

ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"


def load_accounts() -> dict:
    """Read accounts.json and return its contents as a dictionary."""
    if not ACCOUNTS_FILE.exists():
        raise FileNotFoundError(f"Cannot find {ACCOUNTS_FILE}. Make sure it exists.")
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)


def get_wallet(account_key: str) -> Wallet:
    """
    Return an xrpl Wallet object for the given account key.

    account_key must be one of:
        "issuer_treasury", "operator_a", "operator_b"
    """
    accounts = load_accounts()

    if account_key not in accounts:
        raise KeyError(f"'{account_key}' not found in accounts.json. "
                       f"Available keys: {list(accounts.keys())}")

    entry = accounts[account_key]
    secret = entry.get("secret", "")

    if not secret:
        raise ValueError(
            f"The secret for '{account_key}' ({entry.get('label', account_key)}) "
            "is empty. Please fill in accounts.json with your testnet credentials."
        )

    return Wallet.from_seed(secret)


def print_summary():
    """Print a summary of all accounts (addresses only, never secrets)."""
    accounts = load_accounts()
    print("=== Loaded Accounts ===")
    for key, entry in accounts.items():
        address = entry.get("address") or "(empty)"
        label = entry.get("label", key)
        print(f"  {label:20s}  {address}")
    print("=======================")


if __name__ == "__main__":
    print_summary()
