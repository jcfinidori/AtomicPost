"""
03_settlement_payment.py

UAE Post (Operator A) sends 15,300 USDST to Kenya Post (Operator B)
to settle a net bilateral obligation.

The Treasury pre-funded UAE Post in the previous step.
UAE Post now discharges that obligation by paying Kenya Post.

A Memo is attached with the payment instruction reference —
this becomes the immutable, auditable record of the settlement.

Run with:
    python 03_settlement_payment.py
"""

import json
import time
from xrpl.clients import WebsocketClient
from xrpl.models.transactions import Payment, Memo
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.transaction import submit_and_wait
from accounts import get_wallet, load_accounts

TESTNET_URL    = "wss://s.altnet.rippletest.net:51233"
CURRENCY       = "5553445354000000000000000000000000000000"  # "USDST" in hex
CURRENCY_LABEL = "USDST"
SETTLE_AMOUNT  = "15300"

MEMO_PAYLOAD = {
    "SettlementCycleID":      "CYCLE_2026-02-05",
    "PaymentInstructionID":   "PI_77102",
    "TreasuryApprovalID":     "TA_88421",
    "PartialPayment":         "N",
}


def to_hex(text: str) -> str:
    return text.encode("utf-8").hex().upper()


def build_memo() -> Memo:
    memo_json = json.dumps(MEMO_PAYLOAD, separators=(",", ":"))
    return Memo(
        memo_type=to_hex("settlement/postal"),
        memo_format=to_hex("application/json"),
        memo_data=to_hex(memo_json),
    )


def main():
    accounts      = load_accounts()
    sender_wallet = get_wallet("operator_a")           # UAE Post (holds the USDST)
    dest_address  = accounts["operator_b"]["address"]  # Kenya Post (receives settlement)
    issuer_address = accounts["issuer_treasury"]["address"]

    print("=" * 60)
    print("  USDST Settlement Payment — XRPL Testnet")
    print("=" * 60)
    print(f"  From     : UAE Post    ({sender_wallet.address})")
    print(f"  To       : Kenya Post  ({dest_address})")
    print(f"  Amount   : {SETTLE_AMOUNT} {CURRENCY_LABEL}")
    print(f"  Memo     : {json.dumps(MEMO_PAYLOAD, indent=4)}")
    print("=" * 60)

    print("\n  Executing settlement... this replaces a 7-30 day")
    print("  correspondent banking process\n")

    start_time = time.time()

    with WebsocketClient(TESTNET_URL) as client:
        tx = Payment(
            account=sender_wallet.address,
            destination=dest_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=SETTLE_AMOUNT,
            ),
            memos=[build_memo()],
        )

        response  = submit_and_wait(tx, client, sender_wallet)
        result    = response.result

    elapsed   = round(time.time() - start_time, 2)
    tx_hash   = result["hash"]
    tx_result = result["meta"]["TransactionResult"]
    validated = result.get("validated", False)

    print(f"  Sender    : UAE Post    ({sender_wallet.address})")
    print(f"  Receiver  : Kenya Post  ({dest_address})")
    print(f"  Amount    : {SETTLE_AMOUNT} {CURRENCY_LABEL}")
    print(f"  Result    : {tx_result}")
    print(f"  Tx Hash   : {tx_hash}")
    print(f"  Finalized : {'YES — ledger-validated' if validated else 'PENDING'}")
    print(f"  Time      : {elapsed}s  (vs 7–30 days via correspondent banking)")

    if tx_result != "tesSUCCESS":
        raise RuntimeError(f"Settlement failed: {tx_result}")

    print("\n  Settlement complete.")
    print("=" * 60)
    print(f"\n  Verify on the testnet explorer:")
    print(f"  https://testnet.xrpl.org/transactions/{tx_hash}")
    print()


if __name__ == "__main__":
    main()
