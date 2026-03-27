"""
04_partial_settlement.py

Kenya Post (Operator B) has a 15,300 USDST obligation to UAE Post
but only has liquidity for 10,000 USDST this cycle.

10,000 is settled now. The remaining 5,300 is recorded in the
Memo as a carry-forward obligation for the next cycle.

Run with:
    python 04_partial_settlement.py
"""

import json
import time
from xrpl.clients import WebsocketClient
from xrpl.models.transactions import Payment, Memo
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.transaction import submit_and_wait
from accounts import get_wallet, load_accounts

TESTNET_URL       = "wss://s.altnet.rippletest.net:51233"
CURRENCY          = "5553445354000000000000000000000000000000"  # "USDST" in hex
CURRENCY_LABEL    = "USDST"

TOTAL_OBLIGATION  = 15300
AMOUNT_TO_SETTLE  = 10000
REMAINING         = TOTAL_OBLIGATION - AMOUNT_TO_SETTLE  # 5300

MEMO_PAYLOAD = {
    "SettlementCycleID":       "CYCLE_2026-02-05",
    "PaymentInstructionID":    "PI_77103",
    "TreasuryApprovalID":      "TA_88421",
    "PartialPayment":          "Y",
    "PartialCarryForwardFlag": "Y",
    "RemainingObligation":     str(REMAINING),
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
    sender_wallet = get_wallet("operator_b")           # Kenya Post
    dest_address  = accounts["operator_a"]["address"]  # UAE Post
    issuer_address = accounts["issuer_treasury"]["address"]

    print("=" * 60)
    print("  USDST Partial Settlement — XRPL Testnet")
    print("=" * 60)
    print(f"  From     : Kenya Post  ({sender_wallet.address})")
    print(f"  To       : UAE Post    ({dest_address})")
    print(f"  Sending  : {AMOUNT_TO_SETTLE:,} {CURRENCY_LABEL}  "
          f"(of {TOTAL_OBLIGATION:,} total obligation)")
    print(f"  Carry fwd: {REMAINING:,} {CURRENCY_LABEL}")
    print(f"  Memo     : {json.dumps(MEMO_PAYLOAD, indent=4)}")
    print("=" * 60)

    start_time = time.time()

    with WebsocketClient(TESTNET_URL) as client:
        tx = Payment(
            account=sender_wallet.address,
            destination=dest_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=str(AMOUNT_TO_SETTLE),
            ),
            memos=[build_memo()],
        )

        print("\n  Submitting partial settlement...")
        response  = submit_and_wait(tx, client, sender_wallet)
        result    = response.result

    elapsed   = round(time.time() - start_time, 2)
    tx_hash   = result["hash"]
    tx_result = result["meta"]["TransactionResult"]
    validated = result.get("validated", False)

    if tx_result != "tesSUCCESS":
        raise RuntimeError(f"Partial settlement failed: {tx_result}")

    print(f"\n  Result    : {tx_result}")
    print(f"  Tx Hash   : {tx_hash}")
    print(f"  Finalized : {'YES — ledger-validated' if validated else 'PENDING'}")
    print(f"  Time      : {elapsed}s")

    print()
    print("  ── Settlement Summary ─────────────────────────────")
    print(f"  Original obligation     : {TOTAL_OBLIGATION:>8,} {CURRENCY_LABEL}")
    print(f"  Amount settled          : {AMOUNT_TO_SETTLE:>8,} {CURRENCY_LABEL}")
    print(f"  Remaining carried fwd   : {REMAINING:>8,} {CURRENCY_LABEL}")
    print(f"  Status                  : Partial_Settled")
    print(f"  Linked cycle            : {MEMO_PAYLOAD['SettlementCycleID']}")
    print(f"  Payment instruction     : {MEMO_PAYLOAD['PaymentInstructionID']}")
    print("  ───────────────────────────────────────────────────")

    print(f"\n  Verify on the testnet explorer:")
    print(f"  https://testnet.xrpl.org/transactions/{tx_hash}")
    print()


if __name__ == "__main__":
    main()
