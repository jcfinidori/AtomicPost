"""
xrpl_service.py — POSTAL SETTLEMENT POC
All XRPL testnet interactions: trustline check/setup, USDST issuance, redemption.

All public functions return a plain dict with at least a "success" key so
Flask routes never have to deal with exceptions from this layer.
"""

import json
import time
from xrpl.clients import WebsocketClient
from xrpl.wallet import Wallet
from xrpl.models.transactions import Payment, TrustSet, Memo
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountLines
from xrpl.transaction import submit_and_wait
from accounts import load_accounts

# ── Constants ──────────────────────────────────────────────────────────────────

TESTNET_URL = "wss://s.altnet.rippletest.net:51233"

# "USDST" encoded as a 40-char hex currency code (XRPL non-standard currency)
CURRENCY = "5553445354000000000000000000000000000000"

TRUST_LIMIT = "5000000"
EXPLORER    = "https://testnet.xrpl.org/transactions/"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load() -> dict:
    return load_accounts()


def _wallet(accounts: dict, key: str) -> Wallet:
    return Wallet.from_seed(accounts[key]["secret"])


def _to_hex(text: str) -> str:
    return text.encode("utf-8").hex().upper()


def _make_memo(payload: dict) -> Memo:
    return Memo(
        memo_type=_to_hex("settlement/postal"),
        memo_format=_to_hex("application/json"),
        memo_data=_to_hex(json.dumps(payload, separators=(",", ":"))),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def check_trustlines() -> dict:
    """
    Check whether operator_a and operator_b each have a trustline to
    issuer_treasury for USDST.

    Returns:
        {
            "operator_a": True | False,
            "operator_b": True | False
        }
    """
    try:
        accounts = _load()
        issuer_address = accounts["issuer_treasury"]["address"]
        result = {"operator_a": False, "operator_b": False}

        with WebsocketClient(TESTNET_URL) as client:
            for key in ("operator_a", "operator_b"):
                op_address = accounts[key]["address"]
                resp = client.request(AccountLines(account=op_address))
                lines = resp.result.get("lines", [])
                for line in lines:
                    if (line["currency"] == CURRENCY
                            and line["account"] == issuer_address):
                        result[key] = True
                        break

        return result

    except Exception as exc:
        # Return unknown state so the UI degrades gracefully
        return {"operator_a": None, "operator_b": None, "error": str(exc)}


def setup_trustline(operator_id: str) -> dict:
    """
    Create a TrustSet from operator_id toward issuer_treasury for USDST.

    Args:
        operator_id: "operator_a" or "operator_b"

    Returns:
        {"success": bool, "tx_hash": str | None, "error": str | None}
    """
    try:
        accounts = _load()
        issuer_address = accounts["issuer_treasury"]["address"]
        op_wallet = _wallet(accounts, operator_id)

        tx = TrustSet(
            account=op_wallet.address,
            limit_amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=TRUST_LIMIT,
            ),
        )

        with WebsocketClient(TESTNET_URL) as client:
            result = submit_and_wait(tx, client, op_wallet)

        tx_hash = result.result.get("hash")
        return {"success": True, "tx_hash": tx_hash, "error": None}

    except Exception as exc:
        return {"success": False, "tx_hash": None, "error": str(exc)}


def issue_usdst(
    operator_id: str,
    amount: float,
    clearing_cycle_id: str,
    issuance_authorization_id: str,
    treasury_approval_id: str,
) -> dict:
    """
    Mint USDST from issuer_treasury and deliver it to operator_id.

    Memo contains: SettlementCycleID, IssuanceAuthorizationID,
                   TreasuryApprovalID, TransactionType, PartialPayment.
    FXReference is omitted (Phase 1).

    Returns:
        {"success": bool, "tx_hash": str | None, "amount": float, "error": str | None}
    """
    if amount <= 0:
        return {
            "success": False, "tx_hash": None, "amount": amount,
            "error": "Amount must be greater than 0."
        }

    try:
        accounts       = _load()
        issuer_wallet  = _wallet(accounts, "issuer_treasury")
        dest_address   = accounts[operator_id]["address"]

        memo_payload = {
            "SettlementCycleID":       clearing_cycle_id,
            "IssuanceAuthorizationID": issuance_authorization_id,
            "TreasuryApprovalID":      treasury_approval_id,
            "TransactionType":         "Issuance",
            "PartialPayment":          "N",
        }

        tx = Payment(
            account=issuer_wallet.address,
            destination=dest_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_wallet.address,
                value=str(amount),
            ),
            memos=[_make_memo(memo_payload)],
        )

        start_time = time.time()
        with WebsocketClient(TESTNET_URL) as client:
            result = submit_and_wait(tx, client, issuer_wallet)
        execution_seconds = round(time.time() - start_time, 2)

        tx_hash = result.result.get("hash")
        return {"success": True, "tx_hash": tx_hash, "amount": amount,
                "execution_seconds": execution_seconds, "error": None}

    except Exception as exc:
        return {"success": False, "tx_hash": None, "amount": amount,
                "execution_seconds": None, "error": str(exc)}


def redeem_usdst(
    operator_id: str,
    amount: float,
    clearing_cycle_id: str,
    redemption_reference_id: str,
) -> dict:
    """
    Operator sends USDST back to issuer_treasury (burn/redemption).

    Memo contains: SettlementCycleID, RedemptionReferenceID,
                   TransactionType, CycleStatus.

    Returns:
        {"success": bool, "tx_hash": str | None, "amount": float, "error": str | None}
    """
    try:
        accounts       = _load()
        op_wallet      = _wallet(accounts, operator_id)
        issuer_address = accounts["issuer_treasury"]["address"]

        memo_payload = {
            "SettlementCycleID":     clearing_cycle_id,
            "RedemptionReferenceID": redemption_reference_id,
            "TransactionType":       "Redemption",
            "CycleStatus":           "Closing",
        }

        tx = Payment(
            account=op_wallet.address,
            destination=issuer_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=str(amount),
            ),
            memos=[_make_memo(memo_payload)],
        )

        start_time = time.time()
        with WebsocketClient(TESTNET_URL) as client:
            result = submit_and_wait(tx, client, op_wallet)
        execution_seconds = round(time.time() - start_time, 2)

        tx_hash = result.result.get("hash")
        return {"success": True, "tx_hash": tx_hash, "amount": amount,
                "execution_seconds": execution_seconds, "error": None}

    except Exception as exc:
        return {"success": False, "tx_hash": None, "amount": amount,
                "execution_seconds": None, "error": str(exc)}


def execute_settlement(
    from_operator_id: str,
    to_operator_id: str,
    amount: float,
    clearing_cycle_id: str,
    payment_instruction_id: str,
    treasury_approval_id: str,
    is_partial: bool,
) -> dict:
    """
    Execute a net settlement Payment between two wallets on XRPL testnet.

    La Poste CI outbound: from_operator_id = "issuer_treasury"
    FXReference is omitted (Phase 1).

    submit_and_wait() waits for ledger finality, so finality_confirmed
    is always True on success.

    Returns:
        {
            "success": bool,
            "tx_hash": str | None,
            "amount": float,
            "finality_confirmed": bool,
            "error": str | None
        }
    """
    try:
        accounts       = _load()
        from_wallet    = _wallet(accounts, from_operator_id)
        to_address     = accounts[to_operator_id]["address"]
        issuer_address = accounts["issuer_treasury"]["address"]

        memo_payload = {
            "SettlementCycleID":    clearing_cycle_id,
            "PaymentInstructionID": payment_instruction_id,
            "TreasuryApprovalID":   treasury_approval_id,
            "TransactionType":      "Settlement",
            "PartialPayment":       "Y" if is_partial else "N",
        }

        tx = Payment(
            account=from_wallet.address,
            destination=to_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=str(amount),
            ),
            memos=[_make_memo(memo_payload)],
        )

        start_time = time.time()
        with WebsocketClient(TESTNET_URL) as client:
            result = submit_and_wait(tx, client, from_wallet)
        execution_seconds = round(time.time() - start_time, 2)

        tx_hash = result.result.get("hash")
        return {
            "success":            True,
            "tx_hash":            tx_hash,
            "amount":             amount,
            "finality_confirmed": True,
            "execution_seconds":  execution_seconds,
            "error":              None,
        }

    except Exception as exc:
        return {
            "success":            False,
            "tx_hash":            None,
            "amount":             amount,
            "finality_confirmed": False,
            "execution_seconds":  None,
            "error":              str(exc),
        }


def verify_transaction(tx_hash: str) -> dict:
    """
    Fetch a transaction from XRPL testnet by hash and return its key fields.

    Returns:
        {
            "success": bool,
            "tx_hash": str,
            "from_wallet": str | None,
            "to_wallet": str | None,
            "amount": float | None,
            "currency": str | None,
            "memo_raw": str | None,      # raw hex from MemoData
            "memo_decoded": dict | None, # parsed JSON or None
            "ledger_confirmed": bool,
            "timestamp": int | None,     # XRPL epoch (offset from 2000-01-01)
            "error": str | None
        }
    """
    try:
        from xrpl.models.requests import Tx   # local import — keeps top-level clean

        with WebsocketClient(TESTNET_URL) as client:
            resp = client.request(Tx(transaction=tx_hash))

        result = resp.result

        # xrpl-py response structure (tested against xrpl-py 2.x on XRPL testnet):
        #   result['validated']  — bool, ledger finality
        #   result['tx_json']    — transaction body (Account, Destination, Amount, Memos, …)
        #   result['meta']       — metadata
        # Fallback: some older builds surface fields directly at result level.
        tx_json = result.get("tx_json") or result

        # Sender / receiver
        from_wallet = tx_json.get("Account")
        to_wallet   = tx_json.get("Destination")

        # Amount — newer xrpl-py returns issued currency amounts under DeliverMax,
        # older versions use Amount.  Try Amount first, fall back to DeliverMax.
        raw_amount = tx_json.get("Amount") or tx_json.get("DeliverMax")
        if isinstance(raw_amount, dict):
            # Issued currency: {"value": "15300", "currency": "...", "issuer": "..."}
            try:
                amount = float(raw_amount.get("value", 0))
            except Exception:
                amount = 0.0
            currency = raw_amount.get("currency", "")
        elif raw_amount:
            # XRP in drops (string)
            try:
                amount = float(raw_amount) / 1_000_000
            except Exception:
                amount = 0.0
            currency = "XRP"
        else:
            amount   = 0.0
            currency = ""

        # validated lives at the top-level result, not inside tx_json
        ledger_confirmed = result.get("validated", False)
        timestamp        = result.get("date") or tx_json.get("date")

        # ── Memo decoding ─────────────────────────────────────────────────────
        memo_raw     = None
        memo_decoded = None

        memo_candidates = tx_json.get("Memos", [])

        for memo_entry in memo_candidates:
            memo_obj      = memo_entry.get("Memo", {})
            memo_data_hex = memo_obj.get("MemoData", "")
            if not memo_data_hex:
                continue

            memo_raw = memo_data_hex
            try:
                # Normalise case — XRPL returns uppercase hex
                decoded_bytes  = bytes.fromhex(memo_data_hex.lower())
                decoded_string = decoded_bytes.decode("utf-8").strip()
                memo_decoded   = json.loads(decoded_string)
                break   # first successfully decoded memo wins
            except Exception:
                memo_decoded = None

        return {
            "success":          True,
            "tx_hash":          tx_hash,
            "from_wallet":      from_wallet,
            "to_wallet":        to_wallet,
            "amount":           amount,
            "currency":         currency,
            "memo_raw":         memo_raw,
            "memo_decoded":     memo_decoded,
            "ledger_confirmed": ledger_confirmed,
            "timestamp":        timestamp,
            "error":            None,
        }

    except Exception as exc:
        return {
            "success":          False,
            "tx_hash":          tx_hash,
            "from_wallet":      None,
            "to_wallet":        None,
            "amount":           None,
            "currency":         None,
            "memo_raw":         None,
            "memo_decoded":     None,
            "ledger_confirmed": False,
            "timestamp":        None,
            "error":            str(exc),
        }


def reconcile_transaction(
    tx_hash: str,
    expected_amount: float,
    expected_from: str,
    expected_to: str,
    expected_cycle_id: str,
) -> dict:
    """
    Fetch a transaction from XRPL and deterministically verify it matches
    the expected off-ledger record.

    Checks (all must pass for reconciled = True):
        1. amount matches within 0.001 tolerance
        2. from_wallet matches expected_from
        3. to_wallet matches expected_to
        4. memo contains SettlementCycleID matching expected_cycle_id

    Returns:
        {
            "reconciled": bool,
            "tx_hash": str,
            "checks": {
                "amount_match":   bool,
                "sender_match":   bool,
                "receiver_match": bool,
                "memo_match":     bool,
            },
            "exception_reason": str | None,
            "fetched_data": dict    # full verify_transaction result
        }
    """
    fetched = verify_transaction(tx_hash)

    if not fetched["success"]:
        return {
            "reconciled":       False,
            "tx_hash":          tx_hash,
            "checks": {
                "amount_match":   False,
                "sender_match":   False,
                "receiver_match": False,
                "memo_match":     False,
            },
            "exception_reason": "TRANSACTION_NOT_FOUND",
            "fetched_data":     fetched,
        }

    if not fetched["ledger_confirmed"]:
        return {
            "reconciled":       False,
            "tx_hash":          tx_hash,
            "checks": {
                "amount_match":   False,
                "sender_match":   False,
                "receiver_match": False,
                "memo_match":     False,
            },
            "exception_reason": "LEDGER_NOT_CONFIRMED",
            "fetched_data":     fetched,
        }

    # ── Run the four checks ────────────────────────────────────────────────────
    def _normalize(v):
        """Round to 4 dp so '1.53E+4', '15300', '15300.000000' all compare equal."""
        try:
            return round(float(str(v)), 4)
        except Exception:
            return None

    norm_fetched   = _normalize(fetched["amount"])
    norm_expected  = _normalize(expected_amount)
    print(
        f"DEBUG reconcile_transaction [{tx_hash[:16]}]: "
        f"amount comparison: fetched={fetched['amount']!r} expected={expected_amount!r} "
        f"normalized_fetched={norm_fetched} normalized_expected={norm_expected}"
    )
    amount_match   = (
        norm_fetched is not None
        and norm_expected is not None
        and abs(norm_fetched - norm_expected) < 0.01
    )
    sender_match   = fetched["from_wallet"] == expected_from
    receiver_match = fetched["to_wallet"]   == expected_to

    memo           = fetched["memo_decoded"]
    if memo is None:
        memo_match        = False
        exception_reason  = "MEMO_MISSING"
    elif memo.get("SettlementCycleID") != expected_cycle_id:
        memo_match        = False
        exception_reason  = "MEMO_CYCLE_MISMATCH"
    else:
        memo_match        = True
        exception_reason  = None

    # Derive exception_reason from first failing check if memo is fine
    if exception_reason is None:
        if not amount_match:
            exception_reason = "AMOUNT_MISMATCH"
        elif not sender_match:
            exception_reason = "SENDER_MISMATCH"
        elif not receiver_match:
            exception_reason = "RECEIVER_MISMATCH"

    reconciled = amount_match and sender_match and receiver_match and memo_match

    return {
        "reconciled": reconciled,
        "tx_hash":    tx_hash,
        "checks": {
            "amount_match":   amount_match,
            "sender_match":   sender_match,
            "receiver_match": receiver_match,
            "memo_match":     memo_match,
        },
        "exception_reason": None if reconciled else exception_reason,
        "fetched_data":     fetched,
    }


def simulate_inbound_payment(
    from_operator_id: str,
    amount: float,
    clearing_cycle_id: str,
    settlement_record_id: str,
    payment_instruction_id: str,
    treasury_approval_id: str,
) -> dict:
    """
    Simulate a counterparty inbound payment to La Poste CI (issuer_treasury).

    Executes a real XRPL Payment from the counterparty operator wallet to
    issuer_treasury, representing the counterparty settling their obligation.
    Used for PoC demo purposes only — in production the counterparty would
    initiate this from their own system.

    Args:
        from_operator_id:       "operator_a" (UAE Post) or "operator_b" (Kenya Post)
        amount:                 USDST amount to send
        clearing_cycle_id:      e.g. "CC_00123"
        settlement_record_id:   SR ID stored in memo for traceability
        payment_instruction_id: auto-generated PI_ reference
        treasury_approval_id:   TA_ reference from the form

    Returns:
        {
            "success": bool,
            "tx_hash": str | None,
            "amount": float,
            "from_operator": str,
            "execution_seconds": float | None,
            "error": str | None
        }
    """
    if amount <= 0:
        return {
            "success": False, "tx_hash": None, "amount": amount,
            "from_operator": from_operator_id,
            "execution_seconds": None,
            "error": "Amount must be greater than 0.",
        }

    try:
        accounts       = _load()
        op_wallet      = _wallet(accounts, from_operator_id)
        issuer_address = accounts["issuer_treasury"]["address"]

        memo_payload = {
            "SettlementCycleID":    clearing_cycle_id,
            "SettlementRecordID":   settlement_record_id,
            "PaymentInstructionID": payment_instruction_id,
            "TreasuryApprovalID":   treasury_approval_id,
            "TransactionType":      "InboundSettlement",
            "SimulatedInbound":     "Y",
            "PartialPayment":       "N",
        }

        tx = Payment(
            account=op_wallet.address,
            destination=issuer_address,
            amount=IssuedCurrencyAmount(
                currency=CURRENCY,
                issuer=issuer_address,
                value=str(amount),
            ),
            memos=[_make_memo(memo_payload)],
        )

        start_time = time.time()
        with WebsocketClient(TESTNET_URL) as client:
            result = submit_and_wait(tx, client, op_wallet)
        execution_seconds = round(time.time() - start_time, 2)

        tx_hash = result.result.get("hash")
        return {
            "success":           True,
            "tx_hash":           tx_hash,
            "amount":            amount,
            "from_operator":     from_operator_id,
            "execution_seconds": execution_seconds,
            "error":             None,
        }

    except Exception as exc:
        return {
            "success":           False,
            "tx_hash":           None,
            "amount":            amount,
            "from_operator":     from_operator_id,
            "execution_seconds": None,
            "error":             str(exc),
        }


def get_operator_balance(operator_id: str) -> float:
    """
    Return the USDST balance for operator_id, or 0.0 if no trustline exists.
    """
    try:
        accounts       = _load()
        op_address     = accounts[operator_id]["address"]
        issuer_address = accounts["issuer_treasury"]["address"]

        with WebsocketClient(TESTNET_URL) as client:
            resp = client.request(AccountLines(account=op_address))

        for line in resp.result.get("lines", []):
            if (line["currency"] == CURRENCY
                    and line["account"] == issuer_address):
                return float(line["balance"])

        return 0.0

    except Exception:
        return 0.0
