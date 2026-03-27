"""
05_redemption_burn.py

UAE Post (Operator A) returns its USDST balance to the Issuer/Treasury.
When tokens flow back to the issuer, they are effectively destroyed —
the issuer's liability is extinguished and the stablecoin supply shrinks.

Note on the 15,300 cycle total:
  - UAE Post holds 10,000 USDST  → redeemed here
  - Kenya Post holds  5,300 USDST → remaining carry-forward (PI_77103)
  The 10,000 redemption closes UAE Post's position for this cycle.

Run with:
    python 05_redemption_burn.py
"""

import json
import time
from xrpl.clients import WebsocketClient
from xrpl.models.transactions import Payment, Memo
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountLines
from xrpl.transaction import submit_and_wait
from accounts import get_wallet, load_accounts

TESTNET_URL    = "wss://s.altnet.rippletest.net:51233"
CURRENCY       = "5553445354000000000000000000000000000000"  # "USDST" in hex
CURRENCY_LABEL = "USDST"
CYCLE_ID       = "CYCLE_2026-02-05"

MEMO_PAYLOAD = {
    "SettlementCycleID":     CYCLE_ID,
    "RedemptionReferenceID": "RR_99301",
    "CycleStatus":           "Closing",
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


def get_usdst_balance(client, address: str, issuer_address: str) -> str:
    """Return the USDST balance for an address, as a string."""
    resp  = client.request(AccountLines(account=address))
    for line in resp.result.get("lines", []):
        if line["currency"] == CURRENCY and line["account"] == issuer_address:
            return line["balance"]
    return "0"


def main():
    accounts       = load_accounts()
    sender_wallet  = get_wallet("operator_a")                    # UAE Post
    issuer_address = accounts["issuer_treasury"]["address"]

    print("=" * 60)
    print("  USDST Redemption — XRPL Testnet")
    print("=" * 60)
    print(f"  From   : UAE Post       ({sender_wallet.address})")
    print(f"  To     : Issuer/Treasury ({issuer_address})")
    print(f"  Memo   : {json.dumps(MEMO_PAYLOAD, indent=4)}")
    print("=" * 60)

    with WebsocketClient(TESTNET_URL) as client:

        # Read the live balance so we redeem exactly what is held
        redeem_amount = get_usdst_balance(client, sender_wallet.address, issuer_address)

        if redeem_amount == "0":
            print("\n  No USDST balance to redeem. Exiting.")
            return

        print(f"\n  Live balance to redeem: {redeem_amount} {CURRENCY_LABEL}")
        print("  Submitting redemption...")

        start_time = time.time()

        tx = Payment(
            account=sender_wallet.address,
            destination=issuer_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=redeem_amount,
            ),
            memos=[build_memo()],
        )

        response  = submit_and_wait(tx, client, sender_wallet)
        result    = response.result

    elapsed   = round(time.time() - start_time, 2)
    tx_hash   = result["hash"]
    tx_result = result["meta"]["TransactionResult"]
    validated = result.get("validated", False)

    if tx_result != "tesSUCCESS":
        raise RuntimeError(f"Redemption failed: {tx_result}")

    print(f"\n  Result    : {tx_result}")
    print(f"  Tx Hash   : {tx_hash}")
    print(f"  Finalized : {'YES — ledger-validated' if validated else 'PENDING'}")
    print(f"  Time      : {elapsed}s")

    print()
    print("  ── Redemption Summary ─────────────────────────────")
    print(f"  Tokens redeemed             : {redeem_amount} {CURRENCY_LABEL}")
    print(f"  USD backing released        : ${float(redeem_amount):,.0f}")
    print(f"  Stablecoin supply change    : -{redeem_amount} {CURRENCY_LABEL}")
    print(f"  Settlement cycle {CYCLE_ID}: CLOSED (UAE Post position)")
    print(f"  Transaction hash            : {tx_hash}")
    print("  ───────────────────────────────────────────────────")

    print(f"\n  Verify on the testnet explorer:")
    print(f"  https://testnet.xrpl.org/transactions/{tx_hash}")
    print()


if __name__ == "__main__":
    main()
