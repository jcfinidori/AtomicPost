"""
01_setup_trustlines.py

Sets up TrustLines on the XRPL testnet so that UAE Post and Kenya Post
can hold the stablecoin USDST issued by the Issuer/Treasury account.

Each operator signs a TrustSet transaction saying:
  "I trust the Issuer up to 100,000 USDST"

Run with:
    python 01_setup_trustlines.py
"""

from xrpl.clients import WebsocketClient
from xrpl.models.transactions import TrustSet
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.transaction import submit_and_wait
from accounts import get_wallet, load_accounts

TESTNET_URL = "wss://s.altnet.rippletest.net:51233"
TRUST_LIMIT = "100000"

# XRPL only accepts 3-character standard currency codes.
# For longer names like "USDST" we encode the ASCII bytes as a
# 40-character hex string padded to 20 bytes (160 bits).
# "USDST" → 55 53 44 53 54 → padded → 5553445354000000000000000000000000000000
CURRENCY      = "5553445354000000000000000000000000000000"
CURRENCY_LABEL = "USDST"


def setup_trustline(client, operator_key, issuer_address):
    """Submit a TrustSet transaction for one operator."""
    accounts  = load_accounts()
    label     = accounts[operator_key]["label"]
    wallet    = get_wallet(operator_key)

    print(f"\n  Setting up TrustLine for {label} ({wallet.address})...")

    tx = TrustSet(
        account=wallet.address,
        limit_amount=IssuedCurrencyAmount(
            currency=CURRENCY,
            issuer=issuer_address,
            value=TRUST_LIMIT,
        ),
    )

    response = submit_and_wait(tx, client, wallet)
    result   = response.result

    tx_hash   = result["hash"]
    tx_result = result["meta"]["TransactionResult"]

    print(f"  Label    : {label}")
    print(f"  Operator : {wallet.address}")
    print(f"  Issuer   : {issuer_address}")
    print(f"  Currency : {CURRENCY_LABEL}  |  Limit: {TRUST_LIMIT}")
    print(f"  Result   : {tx_result}")
    print(f"  Tx Hash  : {tx_hash}")

    if tx_result != "tesSUCCESS":
        raise RuntimeError(f"TrustLine for {label} failed: {tx_result}")

    return tx_hash


def main():
    accounts       = load_accounts()
    issuer_address = accounts["issuer_treasury"]["address"]

    print("=" * 55)
    print("  USDST TrustLine Setup — XRPL Testnet")
    print("=" * 55)
    print(f"  Issuer/Treasury : {issuer_address}")
    print(f"  Currency        : {CURRENCY_LABEL}")
    print(f"  Trust Limit     : {TRUST_LIMIT} per operator")
    print("=" * 55)

    with WebsocketClient(TESTNET_URL) as client:
        setup_trustline(client, "operator_a", issuer_address)
        setup_trustline(client, "operator_b", issuer_address)

    print("\n  All TrustLines created successfully.")
    print("=" * 55)


if __name__ == "__main__":
    main()
