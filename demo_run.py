"""
demo_run.py — POSTAL SETTLEMENT POC DEMO RUNNER

Runs all five transactions in sequence against the XRPL testnet.
Designed for live demos: each step pauses so you can speak to the audience.

Run with:
    python demo_run.py
"""

import json
import time
from xrpl.clients import WebsocketClient
from xrpl.models.transactions import (
    Payment, TrustSet, TrustSetFlag, AccountSet, AccountSetAsfFlag, Memo
)
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountLines
from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet
from accounts import load_accounts

# ── Config ──────────────────────────────────────────────────────────────────

TESTNET_URL    = "wss://s.altnet.rippletest.net:51233"
CURRENCY       = "5553445354000000000000000000000000000000"  # "USDST" hex-encoded
CURRENCY_LABEL = "USDST"
CYCLE_ID       = "CYCLE_2026-02-05"
TRUST_LIMIT    = "100000"
PAUSE_SECONDS  = 3


# ── Helpers ──────────────────────────────────────────────────────────────────

def to_hex(text: str) -> str:
    return text.encode("utf-8").hex().upper()

def make_memo(payload: dict) -> Memo:
    return Memo(
        memo_type=to_hex("settlement/postal"),
        memo_format=to_hex("application/json"),
        memo_data=to_hex(json.dumps(payload, separators=(",", ":"))),
    )

def get_usdst_balance(client, address: str, issuer_address: str) -> str:
    resp = client.request(AccountLines(account=address))
    for line in resp.result.get("lines", []):
        if line["currency"] == CURRENCY and line["account"] == issuer_address:
            return line["balance"]
    return "0"

def wallet_from_key(accounts: dict, key: str) -> Wallet:
    return Wallet.from_seed(accounts[key]["secret"])

def step_header(n: int, title: str, description: str):
    print()
    print(f"  ┌─ Step {n} of 5 {'─' * 44}")
    print(f"  │  {title}")
    print(f"  │  {description}")
    print(f"  └{'─' * 54}")

def confirmed(label: str, tx_hash: str, elapsed: float) -> str:
    print(f"  ✔  CONFIRMED  ({elapsed}s)")
    print(f"     {label}")
    print(f"     Hash: {tx_hash}")
    return tx_hash


# ── Transactions ─────────────────────────────────────────────────────────────

def step1_trustline_setup(client, accounts):
    """
    Set up TrustLines for both operators and configure the Issuer
    to allow tokens to flow between accounts (DefaultRipple).
    Idempotent — safe to re-run on existing accounts.
    """
    issuer_wallet  = wallet_from_key(accounts, "issuer_treasury")
    issuer_address = accounts["issuer_treasury"]["address"]
    hashes = []

    # 1a. Enable DefaultRipple on Issuer
    tx = AccountSet(
        account=issuer_wallet.address,
        set_flag=AccountSetAsfFlag.ASF_DEFAULT_RIPPLE,
    )
    r = submit_and_wait(tx, client, issuer_wallet)
    hashes.append(r.result["hash"])

    # 1b. TrustLines: each operator opts in to hold USDST
    for key in ["operator_a", "operator_b"]:
        w = wallet_from_key(accounts, key)
        tx = TrustSet(
            account=w.address,
            limit_amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=TRUST_LIMIT,
            ),
        )
        r = submit_and_wait(tx, client, w)
        hashes.append(r.result["hash"])

    # 1c. Clear NoRipple on Issuer's side of each trust line
    for key in ["operator_a", "operator_b"]:
        op_address = accounts[key]["address"]
        tx = TrustSet(
            account=issuer_wallet.address,
            limit_amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=op_address,
                value="0",
            ),
            flags=TrustSetFlag.TF_CLEAR_NO_RIPPLE,
        )
        r = submit_and_wait(tx, client, issuer_wallet)
        hashes.append(r.result["hash"])

    return hashes[-1]   # return final hash for the summary table


def step2_issue(client, accounts):
    """Issuer mints 15,300 USDST and sends it to UAE Post."""
    issuer_wallet  = wallet_from_key(accounts, "issuer_treasury")
    dest_address   = accounts["operator_a"]["address"]

    tx = Payment(
        account=issuer_wallet.address,
        destination=dest_address,
        amount=IssuedCurrencyAmount(
            currency=CURRENCY,
            issuer=issuer_wallet.address,
            value="15300",
        ),
        memos=[make_memo({
            "SettlementCycleID":       CYCLE_ID,
            "TreasuryApprovalID":      "TA_88421",
            "IssuanceAuthorizationID": "IA_55219",
            "PartialPayment":          "N",
        })],
    )
    r = submit_and_wait(tx, client, issuer_wallet)
    return r.result["hash"]


def step3_settlement(client, accounts):
    """UAE Post pays its 15,300 USDST obligation to Kenya Post."""
    sender_wallet  = wallet_from_key(accounts, "operator_a")
    dest_address   = accounts["operator_b"]["address"]
    issuer_address = accounts["issuer_treasury"]["address"]

    tx = Payment(
        account=sender_wallet.address,
        destination=dest_address,
        amount=IssuedCurrencyAmount(
            currency=CURRENCY,
            issuer=issuer_address,
            value="15300",
        ),
        memos=[make_memo({
            "SettlementCycleID":    CYCLE_ID,
            "PaymentInstructionID": "PI_77102",
            "TreasuryApprovalID":   "TA_88421",
            "PartialPayment":       "N",
        })],
    )
    r = submit_and_wait(tx, client, sender_wallet)
    return r.result["hash"]


def step4_partial(client, accounts):
    """Kenya Post settles 10,000 of its 15,300 obligation; 5,300 carries forward."""
    sender_wallet  = wallet_from_key(accounts, "operator_b")
    dest_address   = accounts["operator_a"]["address"]
    issuer_address = accounts["issuer_treasury"]["address"]

    tx = Payment(
        account=sender_wallet.address,
        destination=dest_address,
        amount=IssuedCurrencyAmount(
            currency=CURRENCY,
            issuer=issuer_address,
            value="10000",
        ),
        memos=[make_memo({
            "SettlementCycleID":       CYCLE_ID,
            "PaymentInstructionID":    "PI_77103",
            "TreasuryApprovalID":      "TA_88421",
            "PartialPayment":          "Y",
            "PartialCarryForwardFlag": "Y",
            "RemainingObligation":     "5300",
        })],
    )
    r = submit_and_wait(tx, client, sender_wallet)
    return r.result["hash"]


def step5_redeem(client, accounts):
    """UAE Post returns its USDST balance to the Treasury, closing its position."""
    sender_wallet  = wallet_from_key(accounts, "operator_a")
    issuer_address = accounts["issuer_treasury"]["address"]

    balance = get_usdst_balance(client, sender_wallet.address, issuer_address)
    if balance == "0":
        raise RuntimeError("UAE Post has no USDST to redeem. Was step 3 run first?")

    tx = Payment(
        account=sender_wallet.address,
        destination=issuer_address,
        amount=IssuedCurrencyAmount(
            currency=CURRENCY,
            issuer=issuer_address,
            value=balance,
        ),
        memos=[make_memo({
            "SettlementCycleID":     CYCLE_ID,
            "RedemptionReferenceID": "RR_99301",
            "CycleStatus":           "Closing",
        })],
    )
    r = submit_and_wait(tx, client, sender_wallet)
    return r.result["hash"], balance


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    accounts = load_accounts()
    results  = {}   # step label → tx hash

    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║    POSTAL SETTLEMENT POC — XRPL TESTNET DEMO        ║")
    print("  ║    Clearing Cycle : CYCLE_2026-02-05                 ║")
    print("  ║    Corridor       : UAE Post → Kenya Post            ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  Connecting to XRPL testnet...")

    with WebsocketClient(TESTNET_URL) as client:

        # ── Step 1 ──────────────────────────────────────────────────
        step_header(
            1,
            "TrustLine Setup",
            "Both postal operators opt in to hold USDST issued by the Treasury.",
        )
        t0 = time.time()
        h  = step1_trustline_setup(client, accounts)
        results["Step 1 — TrustLine Setup"] = h
        confirmed("TrustLines active for UAE Post and Kenya Post", h, round(time.time()-t0,1))
        time.sleep(PAUSE_SECONDS)

        # ── Step 2 ──────────────────────────────────────────────────
        step_header(
            2,
            "USDST Issuance  [15,300 USDST → UAE Post]",
            "Treasury mints 15,300 USDST backed by USD and delivers it to UAE Post.",
        )
        t0 = time.time()
        h  = step2_issue(client, accounts)
        results["Step 2 — USDST Issuance"] = h
        confirmed("15,300 USDST issued to UAE Post", h, round(time.time()-t0,1))
        time.sleep(PAUSE_SECONDS)

        # ── Step 3 ──────────────────────────────────────────────────
        step_header(
            3,
            "Settlement Payment  [UAE Post → Kenya Post, 15,300 USDST]",
            "UAE Post discharges its net obligation to Kenya Post in one atomic transaction.",
        )
        t0 = time.time()
        h  = step3_settlement(client, accounts)
        results["Step 3 — Settlement Payment"] = h
        confirmed("15,300 USDST settled: UAE Post → Kenya Post", h, round(time.time()-t0,1))
        time.sleep(PAUSE_SECONDS)

        # ── Step 4 ──────────────────────────────────────────────────
        step_header(
            4,
            "Partial Settlement  [Kenya Post → UAE Post, 10,000 USDST]",
            "Kenya Post settles 10,000 of 15,300 — the 5,300 shortfall is carried forward on-chain.",
        )
        t0 = time.time()
        h  = step4_partial(client, accounts)
        results["Step 4 — Partial Settlement"] = h
        confirmed("10,000 USDST settled  |  5,300 USDST carry-forward recorded", h, round(time.time()-t0,1))
        time.sleep(PAUSE_SECONDS)

        # ── Step 5 ──────────────────────────────────────────────────
        step_header(
            5,
            "Redemption and Burn  [UAE Post → Treasury]",
            "UAE Post returns its USDST to the Treasury. Tokens are burned, USD backing released.",
        )
        t0 = time.time()
        h, redeemed = step5_redeem(client, accounts)
        results["Step 5 — Redemption & Burn"] = h
        confirmed(f"{redeemed} USDST redeemed and burned", h, round(time.time()-t0,1))

    # ── Final summary ────────────────────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║              DEMO COMPLETE — ALL CONFIRMED           ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    for label, tx_hash in results.items():
        short = f"{tx_hash[:10]}...{tx_hash[-6:]}"
        print(f"  ║  {label:<32s}  {short}  ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    print(f"  ║  Cycle {CYCLE_ID}  Status: CLOSED (UAE Post)  ║")
    print(f"  ║  Kenya Post carry-forward: 5,300 USDST (PI_77103)   ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print("  Full transactions on the testnet explorer:")
    for label, tx_hash in results.items():
        print(f"  {label}")
        print(f"    https://testnet.xrpl.org/transactions/{tx_hash}")
    print()


if __name__ == "__main__":
    main()
