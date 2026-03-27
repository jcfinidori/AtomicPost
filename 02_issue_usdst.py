"""
02_issue_usdst.py

The Issuer/Treasury mints and sends 15,300 USDST to UAE Post (Operator A).
A Memo is attached containing settlement cycle metadata, hex-encoded as
required by the XRPL protocol.

Run with:
    python 02_issue_usdst.py
"""

import json
from xrpl.clients import WebsocketClient
from xrpl.models.transactions import Payment, Memo
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.transaction import submit_and_wait
from accounts import get_wallet, load_accounts

TESTNET_URL    = "wss://s.altnet.rippletest.net:51233"
CURRENCY       = "5553445354000000000000000000000000000000"  # "USDST" in hex
CURRENCY_LABEL = "USDST"
ISSUE_AMOUNT   = "15300"

# Memo payload — attached to the transaction as an immutable audit trail
MEMO_PAYLOAD = {
    "SettlementCycleID":        "CYCLE_2026-02-05",
    "TreasuryApprovalID":       "TA_88421",
    "IssuanceAuthorizationID":  "IA_55219",
    "PartialPayment":           "N",
}


def to_hex(text: str) -> str:
    """Encode a plain-text string to uppercase hex, as XRPL requires for memos."""
    return text.encode("utf-8").hex().upper()


def build_memo() -> Memo:
    """
    XRPL memos have three optional hex fields:
      MemoType   — what kind of data this is (like a MIME type)
      MemoFormat — the encoding (e.g. application/json)
      MemoData   — the actual content
    All three must be hex-encoded UTF-8 strings.
    """
    memo_json = json.dumps(MEMO_PAYLOAD, separators=(",", ":"))  # compact JSON

    return Memo(
        memo_type=to_hex("settlement/postal"),
        memo_format=to_hex("application/json"),
        memo_data=to_hex(memo_json),
    )


def main():
    accounts       = load_accounts()
    issuer_address = accounts["issuer_treasury"]["address"]
    dest_address   = accounts["operator_a"]["address"]

    issuer_wallet  = get_wallet("issuer_treasury")
    memo           = build_memo()

    print("=" * 55)
    print("  USDST Issuance — XRPL Testnet")
    print("=" * 55)
    print(f"  From   : Issuer/Treasury  ({issuer_address})")
    print(f"  To     : UAE Post         ({dest_address})")
    print(f"  Amount : {ISSUE_AMOUNT} {CURRENCY_LABEL}")
    print(f"  Memo   : {json.dumps(MEMO_PAYLOAD, indent=4)}")
    print("=" * 55)

    with WebsocketClient(TESTNET_URL) as client:
        tx = Payment(
            account=issuer_wallet.address,
            destination=dest_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_wallet.address,
                value=ISSUE_AMOUNT,
            ),
            memos=[memo],
        )

        print("\n  Submitting transaction...")
        response  = submit_and_wait(tx, client, issuer_wallet)
        result    = response.result

        tx_hash   = result["hash"]
        tx_result = result["meta"]["TransactionResult"]
        validated = result.get("validated", False)

        print(f"\n  Result    : {tx_result}")
        print(f"  Tx Hash   : {tx_hash}")
        print(f"  Amount    : {ISSUE_AMOUNT} {CURRENCY_LABEL}")
        print(f"  Finalized : {'YES — ledger-validated' if validated else 'PENDING'}")

        if tx_result != "tesSUCCESS":
            raise RuntimeError(f"Issuance failed: {tx_result}")

    print("\n  Issuance complete.")
    print("=" * 55)
    print(f"\n  Verify this transaction on the testnet explorer:")
    print(f"  https://testnet.xrpl.org/transactions/{tx_hash}")
    print()


if __name__ == "__main__":
    main()
