"""
app.py — POSTAL SETTLEMENT POC — Flask Web Interface

Runs both the original XRPL step endpoints (unchanged) and the new
multi-screen dashboard interface backed by postal.db.

Run with:
    python app.py
Then open http://127.0.0.1:5000
"""

import csv
import io
import json
import re
import random
import traceback
from datetime import datetime, timezone
from flask import Flask, jsonify, make_response, render_template, request, redirect, url_for, flash

from database import init_db, get_db, log_action, calculate_cycle_metrics
from accounts import load_accounts

# ── Try to import XRPL helpers (graceful degradation if not installed) ─────────
try:
    from xrpl.clients import WebsocketClient
    from demo_run import (
        TESTNET_URL,
        step1_trustline_setup,
        step2_issue,
        step3_settlement,
        step4_partial,
        step5_redeem,
    )
    XRPL_AVAILABLE = True
except ImportError:
    XRPL_AVAILABLE = False

# ── xrpl_service: new per-screen XRPL functions ────────────────────────────────
try:
    import xrpl_service
    XRPL_SERVICE_AVAILABLE = True
except ImportError:
    xrpl_service = None
    XRPL_SERVICE_AVAILABLE = False

# ── App setup ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = "postal-poc-secret-key-2026"

EXPLORER_BASE = "https://testnet.xrpl.org/transactions/"


@app.template_filter("money")
def _money(value):
    """Format a number as '12,500.00' for templates."""
    try:
        return "{:,.2f}".format(float(value or 0))
    except Exception:
        return str(value)


def _time_ago(iso_ts: str) -> str:
    """Convert an ISO timestamp to a human-readable 'X seconds/minutes/hours ago' string."""
    try:
        recorded = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta    = (datetime.now(timezone.utc) - recorded).total_seconds()
        if delta < 60:
            return f"{int(delta)} seconds ago"
        if delta < 3600:
            return f"{int(delta / 60)} minutes ago"
        return f"{int(delta / 3600)} hours ago"
    except Exception:
        return "recently"


def _recent_settlements(limit: int = 5) -> list:
    """Return the last `limit` rows from settlement_metrics, enriched with time_ago."""
    conn  = get_db()
    rows  = [dict(r) for r in conn.execute(
        "SELECT * FROM settlement_metrics ORDER BY RecordedAt DESC LIMIT ?", (limit,)
    ).fetchall()]
    conn.close()
    for r in rows:
        r["time_ago"] = _time_ago(r.get("RecordedAt") or "")
        r["tx_hash_short"] = (r.get("TxHash") or "")[:8]
    return rows

# Load wallet addresses from accounts.json at startup
try:
    with open("accounts.json") as f:
        _raw = json.load(f)
    WALLETS = {
        "issuer_treasury": _raw["issuer_treasury"]["address"],
        "operator_a":      _raw["operator_a"]["address"],   # UAE Post
        "operator_b":      _raw["operator_b"]["address"],   # Kenya Post
        "issuer_label":    _raw["issuer_treasury"].get("label", "Issuer/Treasury"),
        "operator_a_label": _raw["operator_a"].get("label", "UAE Post"),
        "operator_b_label": _raw["operator_b"].get("label", "Kenya Post"),
    }
except (FileNotFoundError, KeyError):
    WALLETS = {}

init_db()


# ── Dashboard helpers ──────────────────────────────────────────────────────────

def _dashboard_context() -> dict:
    """Gather all data needed by dashboard.html."""
    conn = get_db()

    cycles = [dict(r) for r in conn.execute(
        "SELECT * FROM clearing_cycles ORDER BY CreatedAt DESC"
    ).fetchall()]

    active_agg = conn.execute("""
        SELECT
            COALESCE(SUM(USDBackingAmount), 0) AS total_backing,
            COALESCE(SUM(USDSTIssued), 0)      AS total_issued
        FROM clearing_cycles
        WHERE Status = 'Active'
    """).fetchone()

    total_backing = active_agg["total_backing"]
    total_issued  = active_agg["total_issued"]
    available_capacity = max(total_backing - total_issued, 0)
    utilization_pct = (
        round((total_issued / total_backing) * 100, 1) if total_backing > 0 else 0
    )

    settlement_records = [dict(r) for r in conn.execute(
        "SELECT * FROM settlement_records ORDER BY CreatedAt DESC"
    ).fetchall()]

    status_counts_raw = conn.execute("""
        SELECT Status, COUNT(*) AS cnt
        FROM settlement_records
        GROUP BY Status
    """).fetchall()
    status_counts = {r["Status"]: r["cnt"] for r in status_counts_raw}

    audit_entries = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log ORDER BY Timestamp DESC LIMIT 5"
    ).fetchall()]

    # Active cycle (most recent Active one, if any)
    active_cycle = next((c for c in cycles if c["Status"] == "Active"), None)

    # Active issuance authorization for the active cycle
    active_auth = None
    if active_cycle:
        row = conn.execute(
            """SELECT * FROM issuance_authorizations
               WHERE ClearingCycleID = ? AND Status = 'Active'
               ORDER BY CreatedAt DESC LIMIT 1""",
            (active_cycle["ClearingCycleID"],)
        ).fetchone()
        if row:
            active_auth = dict(row)

    conn.close()

    comparison_metrics = (
        calculate_cycle_metrics(active_cycle["ClearingCycleID"])
        if active_cycle else None
    )

    return {
        "cycles":               cycles,
        "active_cycle":         active_cycle,
        "total_backing":        total_backing,
        "total_issued":         total_issued,
        "available_capacity":   available_capacity,
        "utilization_pct":      utilization_pct,
        "settlement_records":   settlement_records,
        "status_counts":        status_counts,
        "audit_entries":        audit_entries,
        "active_auth":          active_auth,
        "wallets":              WALLETS,
        "comparison_metrics":   comparison_metrics,
        "recent_settlements":   _recent_settlements(),
    }


# ── Main dashboard ─────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    ctx = _dashboard_context()
    return render_template("dashboard.html", **ctx)


# ── Placeholder routes for the other 6 screens ────────────────────────────────
# These render stub pages so navigation links work now and can be
# fleshed out in later files.

# ── Clearing Cycle Setup — Screen 2 ───────────────────────────────────────────

_CYCLE_ID_RE = re.compile(r'^CYCLE_\d{4}-\d{2}-\d{2}$')


def _gen_sr_id() -> str:
    """Generate a unique SettlementRecordID like SR_049217."""
    conn = get_db()
    for _ in range(20):
        candidate = f"SR_{random.randint(0, 999999):06d}"
        exists = conn.execute(
            "SELECT 1 FROM settlement_records WHERE SettlementRecordID = ?",
            (candidate,)
        ).fetchone()
        if not exists:
            conn.close()
            return candidate
    conn.close()
    raise RuntimeError("Could not generate a unique SettlementRecordID after 20 attempts")


@app.route("/clearing")
def clearing():
    conn = get_db()
    cycles = [dict(r) for r in conn.execute(
        "SELECT * FROM clearing_cycles ORDER BY CreatedAt DESC"
    ).fetchall()]

    # For each cycle attach its settlement records so the template can render
    # obligation counts and determine whether Close is safe to offer.
    records_raw = [dict(r) for r in conn.execute(
        "SELECT * FROM settlement_records ORDER BY ClearingCycleID, CreatedAt"
    ).fetchall()]
    conn.close()

    # Group records by cycle ID for easy lookup in the template
    records_by_cycle: dict = {}
    for rec in records_raw:
        records_by_cycle.setdefault(rec["ClearingCycleID"], []).append(rec)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return render_template(
        "clearing.html",
        cycles=cycles,
        records_by_cycle=records_by_cycle,
        today=today,
        form={}           # empty dict = no preserved values on first load
    )


@app.route("/clearing/create", methods=["POST"])
def clearing_create():
    cycle_id   = request.form.get("cycle_id", "").strip()
    uae_inc    = "uae_include"    in request.form
    kenya_inc  = "kenya_include"  in request.form
    uae_amt    = request.form.get("uae_amount", "").strip()
    kenya_amt  = request.form.get("kenya_amount", "").strip()
    uae_dir    = request.form.get("uae_direction", "Payable")
    kenya_dir  = request.form.get("kenya_direction", "Payable")

    # Preserve form values for re-render on error
    form = {
        "cycle_id":      cycle_id,
        "uae_include":   uae_inc,
        "uae_amount":    uae_amt,
        "uae_direction": uae_dir,
        "kenya_include": kenya_inc,
        "kenya_amount":  kenya_amt,
        "kenya_direction": kenya_dir,
    }

    # ── Validation ────────────────────────────────────────────────────────────
    errors = []

    if not cycle_id:
        errors.append("Clearing Cycle ID is required.")
    elif not _CYCLE_ID_RE.match(cycle_id):
        errors.append("Clearing Cycle ID must follow the format CYCLE_YYYY-MM-DD.")

    if cycle_id and _CYCLE_ID_RE.match(cycle_id):
        conn = get_db()
        existing = conn.execute(
            "SELECT 1 FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
        ).fetchone()
        conn.close()
        if existing:
            errors.append(f"A clearing cycle with ID '{cycle_id}' already exists.")

    if not uae_inc and not kenya_inc:
        errors.append("At least one counterparty must be included in the cycle.")

    def _validate_amount(included, raw_val, label):
        if not included:
            return None
        try:
            val = float(raw_val)
        except (ValueError, TypeError):
            errors.append(f"{label}: amount must be a number.")
            return None
        if val < 0.01:
            errors.append(f"{label}: amount must be at least 0.01.")
            return None
        return val

    uae_amount   = _validate_amount(uae_inc,   uae_amt,   "UAE Post")
    kenya_amount = _validate_amount(kenya_inc, kenya_amt, "Kenya Post")

    if errors:
        # Re-render form with errors and preserved values
        conn = get_db()
        cycles = [dict(r) for r in conn.execute(
            "SELECT * FROM clearing_cycles ORDER BY CreatedAt DESC"
        ).fetchall()]
        records_raw = [dict(r) for r in conn.execute(
            "SELECT * FROM settlement_records ORDER BY ClearingCycleID, CreatedAt"
        ).fetchall()]
        conn.close()
        records_by_cycle = {}
        for rec in records_raw:
            records_by_cycle.setdefault(rec["ClearingCycleID"], []).append(rec)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for msg in errors:
            flash(msg, "error")
        return render_template(
            "clearing.html",
            cycles=cycles,
            records_by_cycle=records_by_cycle,
            today=today,
            form=form
        )

    # ── Persist ───────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()

    conn.execute(
        """INSERT INTO clearing_cycles
               (ClearingCycleID, CreatedAt, Status, USDBackingAmount, USDSTIssued)
           VALUES (?, ?, 'Active', 0, 0)""",
        (cycle_id, now)
    )

    obligations = []
    if uae_inc and uae_amount is not None:
        obligations.append(("UAE_POST", uae_amount, uae_dir))
    if kenya_inc and kenya_amount is not None:
        obligations.append(("KENYA_POST", kenya_amount, kenya_dir))

    sr_ids = []
    for counterparty, amount, direction in obligations:
        sr_id = _gen_sr_id()
        sr_ids.append(sr_id)
        conn.execute(
            """INSERT INTO settlement_records
                   (SettlementRecordID, ClearingCycleID, CounterpartyID,
                    Amount, Currency, Direction, Status,
                    PartialCarryForwardFlag, CreatedAt, UpdatedAt)
               VALUES (?, ?, ?, ?, 'USDST', ?, 'Pending_Settlement', 'N', ?, ?)""",
            (sr_id, cycle_id, counterparty, amount, direction, now, now)
        )

    conn.commit()
    conn.close()

    details = (
        f"Cycle {cycle_id} created with {len(obligations)} obligation(s): "
        + ", ".join(
            f"{cp} {dir_} {amt:,.2f} USDST (ID: {sid})"
            for (cp, amt, dir_), sid in zip(obligations, sr_ids)
        )
    )
    log_action(cycle_id, "Clearing_Cycle_Created", details)

    flash(
        f"Clearing cycle {cycle_id} created with {len(obligations)} settlement obligation(s).",
        "success"
    )
    return redirect(url_for("clearing"))


@app.route("/clearing/close", methods=["POST"])
def clearing_close():
    cycle_id = request.form.get("cycle_id", "").strip()
    if not cycle_id:
        flash("No cycle ID provided.", "error")
        return redirect(url_for("clearing"))

    conn = get_db()

    # Check the cycle exists and is Active
    cycle = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
    ).fetchone()
    if not cycle:
        conn.close()
        flash(f"Cycle '{cycle_id}' not found.", "error")
        return redirect(url_for("clearing"))
    if cycle["Status"] == "Closed":
        conn.close()
        flash(f"Cycle '{cycle_id}' is already closed.", "error")
        return redirect(url_for("clearing"))

    # All obligations must be Reconciled
    not_reconciled = [
        dict(r) for r in conn.execute(
            """SELECT SettlementRecordID, CounterpartyID, Status
               FROM settlement_records
               WHERE ClearingCycleID = ? AND Status != 'Reconciled'""",
            (cycle_id,)
        ).fetchall()
    ]

    if not_reconciled:
        conn.close()
        blocking = ", ".join(
            f"{r['SettlementRecordID']} ({r['CounterpartyID']}: {r['Status']})"
            for r in not_reconciled
        )
        flash(
            f"Cannot close cycle — {len(not_reconciled)} obligation(s) not yet reconciled: "
            f"{blocking}.",
            "error"
        )
        return redirect(url_for("clearing"))

    conn.execute(
        "UPDATE clearing_cycles SET Status = 'Closed' WHERE ClearingCycleID = ?",
        (cycle_id,)
    )
    conn.commit()
    conn.close()

    log_action(cycle_id, "Clearing_Cycle_Closed",
               f"Cycle {cycle_id} closed. All obligations reconciled.")
    flash(f"Clearing cycle {cycle_id} has been closed successfully.", "success")
    return redirect(url_for("clearing"))

# ── Reserve Governance — Screen 3 ─────────────────────────────────────────────

def _gen_ia_id() -> str:
    """Generate a unique IssuanceAuthorizationID like IA_04917."""
    conn = get_db()
    for _ in range(20):
        candidate = f"IA_{random.randint(0, 99999):05d}"
        exists = conn.execute(
            "SELECT 1 FROM issuance_authorizations WHERE IssuanceAuthorizationID = ?",
            (candidate,)
        ).fetchone()
        if not exists:
            conn.close()
            return candidate
    conn.close()
    raise RuntimeError("Could not generate unique IssuanceAuthorizationID after 20 attempts")


def _reserve_context(selected_cycle_id: str = None) -> dict:
    """Gather all data needed by reserve.html."""
    conn = get_db()

    active_cycles_raw = conn.execute(
        "SELECT * FROM clearing_cycles WHERE Status = 'Active' ORDER BY CreatedAt DESC"
    ).fetchall()

    active_cycles = []
    for row in active_cycles_raw:
        c = dict(row)
        backing  = c["USDBackingAmount"] or 0
        issued   = c["USDSTIssued"] or 0
        avail    = max(backing - issued, 0)
        util_pct = round((issued / backing * 100), 1) if backing > 0 else 0
        c["Available"]      = avail
        c["UtilizationPct"] = util_pct
        active_cycles.append(c)

    authorizations = [dict(r) for r in conn.execute(
        "SELECT * FROM issuance_authorizations ORDER BY CreatedAt DESC"
    ).fetchall()]

    reserve_log = [dict(r) for r in conn.execute(
        """SELECT * FROM audit_log
           WHERE Action LIKE '%USD_Backing%' OR Action LIKE '%Issuance_Authorization%'
           ORDER BY Timestamp DESC LIMIT 10"""
    ).fetchall()]

    conn.close()

    return {
        "active_cycles":    active_cycles,
        "authorizations":   authorizations,
        "reserve_log":      reserve_log,
        "selected_cycle_id": selected_cycle_id,
    }


@app.route("/reserve")
def reserve_governance():
    selected = request.args.get("cycle", None)
    return render_template("reserve.html", **_reserve_context(selected))


@app.route("/reserve/set-backing", methods=["POST"])
def reserve_set_backing():
    cycle_id = request.form.get("cycle_id", "").strip()
    raw_amt  = request.form.get("backing_amount", "").strip()

    # ── Validate ──────────────────────────────────────────────────────────────
    conn = get_db()
    cycle = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
    ).fetchone()

    if not cycle:
        conn.close()
        flash(f"Cycle '{cycle_id}' not found.", "error")
        return redirect(url_for("reserve_governance"))
    if cycle["Status"] != "Active":
        conn.close()
        flash(f"Cycle '{cycle_id}' is not Active.", "error")
        return redirect(url_for("reserve_governance"))

    try:
        amount = float(raw_amt)
    except (ValueError, TypeError):
        conn.close()
        flash("USD Backing Amount must be a valid number.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    if amount <= 0:
        conn.close()
        flash("USD Backing Amount must be greater than 0.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    already_issued = cycle["USDSTIssued"] or 0
    if amount < already_issued:
        conn.close()
        flash(
            f"Cannot set backing to ${amount:,.2f} — "
            f"{already_issued:,.2f} USDST already issued against this cycle.",
            "error"
        )
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    # ── Persist ───────────────────────────────────────────────────────────────
    conn.execute(
        "UPDATE clearing_cycles SET USDBackingAmount = ? WHERE ClearingCycleID = ?",
        (amount, cycle_id)
    )
    conn.commit()
    conn.close()

    log_action(cycle_id, "USD_Backing_Confirmed",
               f"USD backing of ${amount:,.2f} confirmed for {cycle_id}")
    flash(f"USD backing of ${amount:,.2f} confirmed for {cycle_id}.", "success")
    return redirect(url_for("reserve_governance", cycle=cycle_id))


@app.route("/reserve/create-authorization", methods=["POST"])
def reserve_create_authorization():
    cycle_id = request.form.get("cycle_id", "").strip()
    raw_max  = request.form.get("max_issuable", "").strip()

    conn = get_db()
    cycle = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
    ).fetchone()

    if not cycle:
        conn.close()
        flash(f"Cycle '{cycle_id}' not found.", "error")
        return redirect(url_for("reserve_governance"))
    if cycle["Status"] != "Active":
        conn.close()
        flash(f"Cycle '{cycle_id}' is not Active.", "error")
        return redirect(url_for("reserve_governance"))

    backing = cycle["USDBackingAmount"] or 0
    issued  = cycle["USDSTIssued"] or 0
    avail   = max(backing - issued, 0)

    if backing <= 0:
        conn.close()
        flash("USD backing must be confirmed for this cycle before creating an authorization.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    try:
        max_amt = float(raw_max)
    except (ValueError, TypeError):
        conn.close()
        flash("Maximum Issuable Amount must be a valid number.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    if max_amt <= 0:
        conn.close()
        flash("Maximum Issuable Amount must be greater than 0.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    if max_amt > avail:
        conn.close()
        flash(
            f"Maximum Issuable Amount (${max_amt:,.2f}) exceeds "
            f"available capacity (${avail:,.2f}) for {cycle_id}.",
            "error"
        )
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    existing_active = conn.execute(
        """SELECT 1 FROM issuance_authorizations
           WHERE ClearingCycleID = ? AND Status = 'Active'""",
        (cycle_id,)
    ).fetchone()
    if existing_active:
        conn.close()
        flash(f"An Active authorization already exists for {cycle_id}. "
              "Expire it before creating a new one.", "error")
        return redirect(url_for("reserve_governance", cycle=cycle_id))

    # ── Persist ───────────────────────────────────────────────────────────────
    ia_id = _gen_ia_id()
    now   = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO issuance_authorizations
               (IssuanceAuthorizationID, ClearingCycleID, MaximumIssuableAmount,
                AmountIssued, Status, CreatedAt)
           VALUES (?, ?, ?, 0, 'Active', ?)""",
        (ia_id, cycle_id, max_amt, now)
    )
    conn.commit()
    conn.close()

    log_action(cycle_id, "Issuance_Authorization_Created",
               f"Authorization {ia_id} created and active for {cycle_id}. "
               f"Maximum issuable: ${max_amt:,.2f}")
    flash(
        f"Authorization {ia_id} created and active. "
        f"Maximum issuable: ${max_amt:,.2f}.",
        "success"
    )
    return redirect(url_for("reserve_governance", cycle=cycle_id, new_ia=ia_id))


@app.route("/reserve/expire-authorization", methods=["POST"])
def reserve_expire_authorization():
    ia_id = request.form.get("ia_id", "").strip()

    conn = get_db()
    auth = conn.execute(
        "SELECT * FROM issuance_authorizations WHERE IssuanceAuthorizationID = ?",
        (ia_id,)
    ).fetchone()

    if not auth:
        conn.close()
        flash(f"Authorization '{ia_id}' not found.", "error")
        return redirect(url_for("reserve_governance"))

    conn.execute(
        "UPDATE issuance_authorizations SET Status = 'Expired' WHERE IssuanceAuthorizationID = ?",
        (ia_id,)
    )
    conn.commit()
    cycle_id = auth["ClearingCycleID"]
    conn.close()

    log_action(cycle_id, "Issuance_Authorization_Expired",
               f"Authorization {ia_id} manually expired for cycle {cycle_id}.")
    flash(f"Authorization {ia_id} has been expired.", "success")
    return redirect(url_for("reserve_governance", cycle=cycle_id))

# ── Issuance & Redemption — Screen 4 ──────────────────────────────────────────

EXPLORER_BASE_URL = "https://testnet.xrpl.org/transactions/"

OPERATOR_LABELS = {
    "operator_a": "UAE Post",
    "operator_b": "Kenya Post",
}


def _issuance_context() -> dict:
    """Gather all data needed by issuance.html."""
    conn = get_db()

    # Active cycles with computed Available
    active_cycles_raw = conn.execute(
        "SELECT * FROM clearing_cycles WHERE Status = 'Active' ORDER BY CreatedAt DESC"
    ).fetchall()
    active_cycles = []
    for row in active_cycles_raw:
        c = dict(row)
        backing  = c["USDBackingAmount"] or 0
        issued   = c["USDSTIssued"] or 0
        avail    = max(backing - issued, 0)
        util_pct = round((issued / backing * 100), 1) if backing > 0 else 0
        c["Available"]      = avail
        c["UtilizationPct"] = util_pct
        active_cycles.append(c)

    # Active authorizations with computed Remaining
    active_auths_raw = conn.execute(
        """SELECT * FROM issuance_authorizations
           WHERE Status = 'Active' ORDER BY CreatedAt DESC"""
    ).fetchall()
    active_auths = []
    for row in active_auths_raw:
        a = dict(row)
        a["Remaining"] = max(a["MaximumIssuableAmount"] - a["AmountIssued"], 0)
        active_auths.append(a)

    # Recent issuance/redemption transactions
    recent_txs = [dict(r) for r in conn.execute(
        """SELECT * FROM xrpl_transactions
           WHERE TransactionType IN ('Issuance','Redemption')
           ORDER BY ConfirmedAt DESC LIMIT 20"""
    ).fetchall()]

    conn.close()

    # XRPL live data (graceful degradation if XRPL unreachable)
    if XRPL_SERVICE_AVAILABLE:
        trustlines = xrpl_service.check_trustlines()
        balances = {
            "operator_a": xrpl_service.get_operator_balance("operator_a"),
            "operator_b": xrpl_service.get_operator_balance("operator_b"),
        }
    else:
        trustlines = {"operator_a": None, "operator_b": None}
        balances   = {"operator_a": 0.0, "operator_b": 0.0}

    return {
        "active_cycles":  active_cycles,
        "active_auths":   active_auths,
        "trustlines":     trustlines,
        "balances":       balances,
        "recent_txs":     recent_txs,
        "explorer_base":  EXPLORER_BASE_URL,
        "operator_labels": OPERATOR_LABELS,
    }


@app.route("/issuance")
def issuance():
    return render_template("issuance.html", **_issuance_context())


@app.route("/issuance/setup-trustline", methods=["POST"])
def issuance_setup_trustline():
    operator_id = request.form.get("operator_id", "").strip()
    if operator_id not in ("operator_a", "operator_b"):
        flash("Invalid operator ID.", "error")
        return redirect(url_for("issuance"))

    if not XRPL_SERVICE_AVAILABLE:
        flash("XRPL service not available.", "error")
        return redirect(url_for("issuance"))

    result = xrpl_service.setup_trustline(operator_id)
    label  = OPERATOR_LABELS.get(operator_id, operator_id)

    if result["success"]:
        log_action(None, "TrustLine_Setup",
                   f"TrustLine created for {label}. TxHash: {result['tx_hash']}")
        flash(
            f"TrustLine activated for {label}. "
            f"Tx: {result['tx_hash']}",
            "success"
        )
    else:
        flash(f"TrustLine setup failed for {label}: {result['error']}", "error")

    return redirect(url_for("issuance"))


@app.route("/issuance/issue", methods=["POST"])
def issuance_issue():
    cycle_id   = request.form.get("cycle_id", "").strip()
    ia_id      = request.form.get("ia_id", "").strip()
    operator_id = request.form.get("operator_id", "").strip()
    raw_amount = request.form.get("amount", "").strip()
    ta_id      = request.form.get("treasury_approval_id", "").strip()

    # ── DB validation ─────────────────────────────────────────────────────────
    conn = get_db()

    cycle = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
    ).fetchone()
    if not cycle or cycle["Status"] != "Active":
        conn.close()
        flash(f"Cycle '{cycle_id}' is not Active.", "error")
        return redirect(url_for("issuance"))

    auth = conn.execute(
        "SELECT * FROM issuance_authorizations WHERE IssuanceAuthorizationID = ?",
        (ia_id,)
    ).fetchone()
    if not auth or auth["Status"] != "Active":
        conn.close()
        flash(f"Authorization '{ia_id}' is not Active.", "error")
        return redirect(url_for("issuance"))

    if auth["ClearingCycleID"] != cycle_id:
        conn.close()
        flash(f"Authorization {ia_id} does not belong to cycle {cycle_id}.", "error")
        return redirect(url_for("issuance"))

    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        conn.close()
        flash("Amount must be a valid number.", "error")
        return redirect(url_for("issuance"))

    if amount <= 0:
        conn.close()
        flash("Amount must be greater than 0.", "error")
        return redirect(url_for("issuance"))

    auth_remaining  = max(auth["MaximumIssuableAmount"] - auth["AmountIssued"], 0)
    backing         = cycle["USDBackingAmount"] or 0
    issued          = cycle["USDSTIssued"] or 0
    cycle_available = max(backing - issued, 0)

    if amount > auth_remaining:
        conn.close()
        flash(
            f"Amount ${amount:,.2f} exceeds authorization remaining capacity "
            f"${auth_remaining:,.2f}.",
            "error"
        )
        return redirect(url_for("issuance"))

    if amount > cycle_available:
        conn.close()
        flash(
            f"Amount ${amount:,.2f} exceeds cycle available capacity "
            f"${cycle_available:,.2f}.",
            "error"
        )
        return redirect(url_for("issuance"))

    if operator_id not in ("operator_a", "operator_b"):
        conn.close()
        flash("Invalid operator ID.", "error")
        return redirect(url_for("issuance"))

    # Trustline check
    if XRPL_SERVICE_AVAILABLE:
        tl = xrpl_service.check_trustlines()
        if not tl.get(operator_id):
            conn.close()
            label = OPERATOR_LABELS.get(operator_id, operator_id)
            flash(f"No TrustLine exists for {label}. Set it up first.", "error")
            return redirect(url_for("issuance"))
    conn.close()

    if not XRPL_SERVICE_AVAILABLE:
        flash("XRPL service not available.", "error")
        return redirect(url_for("issuance"))

    # ── Submit to XRPL ────────────────────────────────────────────────────────
    result = xrpl_service.issue_usdst(
        operator_id, amount, cycle_id, ia_id, ta_id
    )

    if not result["success"]:
        flash(f"XRPL issuance failed: {result['error']}", "error")
        return redirect(url_for("issuance"))

    tx_hash = result["tx_hash"]
    now     = datetime.now(timezone.utc).isoformat()
    label   = OPERATOR_LABELS.get(operator_id, operator_id)

    # ── Persist ───────────────────────────────────────────────────────────────
    memo_json = json.dumps({
        "SettlementCycleID":       cycle_id,
        "IssuanceAuthorizationID": ia_id,
        "TreasuryApprovalID":      ta_id,
        "TransactionType":         "Issuance",
        "PartialPayment":          "N",
    })

    conn = get_db()

    conn.execute(
        """INSERT INTO xrpl_transactions
               (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
                Amount, MemoJSON, Status, ConfirmedAt)
           VALUES (?, ?, 'Issuance', ?, ?, ?, ?, 'Confirmed', ?)""",
        (tx_hash, cycle_id,
         json.loads(open("accounts.json").read())["issuer_treasury"]["address"],
         json.loads(open("accounts.json").read())[operator_id]["address"],
         amount, memo_json, now)
    )

    conn.execute(
        "UPDATE clearing_cycles SET USDSTIssued = USDSTIssued + ? WHERE ClearingCycleID = ?",
        (amount, cycle_id)
    )

    new_issued = auth["AmountIssued"] + amount
    new_status = "FullyUtilized" if new_issued >= auth["MaximumIssuableAmount"] else "Active"
    conn.execute(
        """UPDATE issuance_authorizations
           SET AmountIssued = ?, Status = ?
           WHERE IssuanceAuthorizationID = ?""",
        (new_issued, new_status, ia_id)
    )

    conn.commit()
    conn.close()

    log_action(cycle_id, "USDST_Issued",
               f"{amount:,.2f} USDST issued to {label} under {ia_id}. "
               f"TxHash: {tx_hash}")
    flash(
        f"Issued {amount:,.2f} USDST to {label}. "
        f"Tx: {tx_hash} — "
        f"<a href='{EXPLORER_BASE_URL}{tx_hash}' target='_blank'>View on XRPL ↗</a>",
        "success"
    )
    return redirect(url_for("issuance"))


@app.route("/issuance/redeem", methods=["POST"])
def issuance_redeem():
    cycle_id    = request.form.get("cycle_id", "").strip()
    operator_id = request.form.get("operator_id", "").strip()
    raw_amount  = request.form.get("amount", "").strip()
    rr_id       = request.form.get("redemption_reference_id", "").strip()

    # Auto-generate redemption reference if blank
    if not rr_id:
        rr_id = f"RR_{random.randint(0, 99999):05d}"

    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        flash("Amount must be a valid number.", "error")
        return redirect(url_for("issuance"))

    if amount <= 0:
        flash("Amount must be greater than 0.", "error")
        return redirect(url_for("issuance"))

    if operator_id not in ("operator_a", "operator_b"):
        flash("Invalid operator ID.", "error")
        return redirect(url_for("issuance"))

    # Balance check
    if XRPL_SERVICE_AVAILABLE:
        balance = xrpl_service.get_operator_balance(operator_id)
        if balance < amount:
            label = OPERATOR_LABELS.get(operator_id, operator_id)
            flash(
                f"{label} only holds {balance:,.2f} USDST — "
                f"cannot redeem {amount:,.2f}.",
                "error"
            )
            return redirect(url_for("issuance"))
    else:
        flash("XRPL service not available.", "error")
        return redirect(url_for("issuance"))

    # ── Submit to XRPL ────────────────────────────────────────────────────────
    result = xrpl_service.redeem_usdst(operator_id, amount, cycle_id, rr_id)

    if not result["success"]:
        flash(f"XRPL redemption failed: {result['error']}", "error")
        return redirect(url_for("issuance"))

    tx_hash = result["tx_hash"]
    now     = datetime.now(timezone.utc).isoformat()
    label   = OPERATOR_LABELS.get(operator_id, operator_id)

    # ── Persist ───────────────────────────────────────────────────────────────
    memo_json = json.dumps({
        "SettlementCycleID":     cycle_id,
        "RedemptionReferenceID": rr_id,
        "TransactionType":       "Redemption",
        "CycleStatus":           "Closing",
    })

    accounts_raw = json.loads(open("accounts.json").read())

    conn = get_db()

    conn.execute(
        """INSERT INTO xrpl_transactions
               (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
                Amount, MemoJSON, Status, ConfirmedAt)
           VALUES (?, ?, 'Redemption', ?, ?, ?, ?, 'Confirmed', ?)""",
        (tx_hash, cycle_id,
         accounts_raw[operator_id]["address"],
         accounts_raw["issuer_treasury"]["address"],
         amount, memo_json, now)
    )

    # Floor USDSTIssued at 0
    conn.execute(
        """UPDATE clearing_cycles
           SET USDSTIssued = MAX(0, USDSTIssued - ?)
           WHERE ClearingCycleID = ?""",
        (amount, cycle_id)
    )
    conn.commit()

    # Check auto-close: issued == 0 AND all obligations reconciled
    cycle = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID = ?", (cycle_id,)
    ).fetchone()

    auto_closed = False
    if cycle and cycle["USDSTIssued"] == 0:
        unreconciled = conn.execute(
            """SELECT COUNT(*) FROM settlement_records
               WHERE ClearingCycleID = ? AND Status != 'Reconciled'""",
            (cycle_id,)
        ).fetchone()[0]
        if unreconciled == 0:
            conn.execute(
                "UPDATE clearing_cycles SET Status = 'Closed' WHERE ClearingCycleID = ?",
                (cycle_id,)
            )
            conn.commit()
            auto_closed = True

    conn.close()

    log_action(cycle_id, "USDST_Redeemed",
               f"{amount:,.2f} USDST redeemed from {label} (ref: {rr_id}). "
               f"TxHash: {tx_hash}")

    if auto_closed:
        log_action(cycle_id, "Cycle_Auto_Closed",
                   f"Cycle {cycle_id} auto-closed: USDSTIssued = 0 and all obligations reconciled.")
        flash(
            f"Redeemed {amount:,.2f} USDST from {label}. "
            f"Cycle {cycle_id} has been automatically closed. "
            f"Tx: {tx_hash}",
            "success"
        )
    else:
        flash(
            f"Redeemed {amount:,.2f} USDST from {label} (ref: {rr_id}). "
            f"Tx: {tx_hash} — "
            f"<a href='{EXPLORER_BASE_URL}{tx_hash}' target='_blank'>View on XRPL ↗</a>",
            "success"
        )

    return redirect(url_for("issuance"))

# ── Settlement Execution — Screen 5 ───────────────────────────────────────────

_COUNTERPARTY_LABELS = {
    "UAE_POST":   "UAE Post",
    "KENYA_POST": "Kenya Post",
}

# Map counterparty DB ID → accounts.json key for wallet lookup
_COUNTERPARTY_WALLET_KEY = {
    "UAE_POST":   "operator_a",
    "KENYA_POST": "operator_b",
}

# Human-readable inbound status labels → DB Status values
_INBOUND_STATUS_MAP = {
    "Not_Received":    "Pending_Settlement",
    "In_Transit":      "Pending_Settlement",
    "Received":        "Settled",
    "Partial_Received": "Partially_Settled",
    "Exception":       "Exception",
}


def _settlement_context() -> dict:
    """Gather all data needed by settlement.html."""
    conn = get_db()

    # All settlement records with counterparty display name attached
    records_raw = [dict(r) for r in conn.execute(
        "SELECT * FROM settlement_records ORDER BY ClearingCycleID, CreatedAt"
    ).fetchall()]

    for rec in records_raw:
        rec["CounterpartyLabel"] = _COUNTERPARTY_LABELS.get(
            rec["CounterpartyID"], rec["CounterpartyID"]
        )

    active_cycles = [dict(r) for r in conn.execute(
        "SELECT * FROM clearing_cycles WHERE Status = 'Active' ORDER BY CreatedAt DESC"
    ).fetchall()]

    # Recent settlement transactions — also parse PI from MemoJSON
    recent_txs_raw = conn.execute(
        """SELECT * FROM xrpl_transactions
           WHERE TransactionType IN ('Settlement', 'PartialSettlement')
           ORDER BY ConfirmedAt DESC LIMIT 30"""
    ).fetchall()
    recent_txs = []
    for row in recent_txs_raw:
        tx = dict(row)
        try:
            memo = json.loads(tx["MemoJSON"]) if tx.get("MemoJSON") else {}
            tx["PaymentInstructionID"] = memo.get("PaymentInstructionID", "—")
        except Exception:
            tx["PaymentInstructionID"] = "—"
        recent_txs.append(tx)

    # Inbound simulation transactions keyed by SettlementRecordID for inline display
    inbound_txs_raw = conn.execute(
        """SELECT * FROM xrpl_transactions
           WHERE TransactionType = 'InboundSettlement'
           ORDER BY ConfirmedAt DESC"""
    ).fetchall()
    inbound_txs_by_sr = {}
    for row in inbound_txs_raw:
        tx = dict(row)
        try:
            memo  = json.loads(tx["MemoJSON"]) if tx.get("MemoJSON") else {}
            sr_id = memo.get("SettlementRecordID")
            if sr_id and sr_id not in inbound_txs_by_sr:
                inbound_txs_by_sr[sr_id] = tx
        except Exception:
            pass

    # Summary stats across all active cycles
    active_ids = [c["ClearingCycleID"] for c in active_cycles]
    payable_total    = 0.0
    receivable_total = 0.0
    settled_count    = 0
    pending_count    = 0
    total_count      = 0

    for rec in records_raw:
        if rec["ClearingCycleID"] not in active_ids:
            continue
        total_count += 1
        if rec["Direction"] == "Payable":
            payable_total += rec["Amount"] or 0
        else:
            receivable_total += rec["Amount"] or 0
        if rec["Status"] == "Settled":
            settled_count += 1
        if rec["Status"] == "Pending_Settlement":
            pending_count += 1

    conn.close()

    payable_records    = [r for r in records_raw if r["Direction"] == "Payable"]
    receivable_records = [r for r in records_raw if r["Direction"] == "Receivable"]

    return {
        "active_cycles":       active_cycles,
        "all_records":         records_raw,
        "payable_records":     payable_records,
        "receivable_records":  receivable_records,
        "recent_txs":          recent_txs,
        "inbound_txs_by_sr":   inbound_txs_by_sr,
        "explorer_base":       EXPLORER_BASE_URL,
        "summary": {
            "total_count":      total_count,
            "payable_total":    payable_total,
            "receivable_total": receivable_total,
            "settled_count":    settled_count,
            "pending_count":    pending_count,
        },
    }


@app.route("/settlement")
def settlement():
    ctx = _settlement_context()
    # Find the cycle being viewed (first active cycle, or first in context)
    cycle_id = None
    for r in ctx.get("payable_records", []) + ctx.get("receivable_records", []):
        if r.get("ClearingCycleID"):
            cycle_id = r["ClearingCycleID"]
            break
    if not cycle_id:
        conn = get_db()
        row = conn.execute(
            "SELECT ClearingCycleID FROM clearing_cycles "
            "WHERE Status='Active' ORDER BY CreatedAt DESC LIMIT 1"
        ).fetchone()
        conn.close()
        cycle_id = row["ClearingCycleID"] if row else None

    ctx["comparison_metrics"] = calculate_cycle_metrics(cycle_id) if cycle_id else None
    ctx["recent_settlements"] = _recent_settlements()
    return render_template("settlement.html", **ctx)


@app.route("/settlement/execute", methods=["POST"])
def settlement_execute():
    sr_id       = request.form.get("settlement_record_id", "").strip()
    pi_id       = request.form.get("payment_instruction_id", "").strip()
    ta_id       = request.form.get("treasury_approval_id", "").strip()
    stype       = request.form.get("settlement_type", "full")
    raw_partial = request.form.get("partial_amount", "").strip()

    # ── Load record ───────────────────────────────────────────────────────────
    conn = get_db()
    record = conn.execute(
        "SELECT * FROM settlement_records WHERE SettlementRecordID = ?", (sr_id,)
    ).fetchone()

    if not record:
        conn.close()
        flash(f"Settlement record '{sr_id}' not found.", "error")
        return redirect(url_for("settlement"))

    if record["Status"] != "Pending_Settlement":
        conn.close()
        flash(f"Record {sr_id} has status '{record['Status']}' — only Pending_Settlement can be executed.", "error")
        return redirect(url_for("settlement"))

    if record["Direction"] != "Payable":
        conn.close()
        flash(f"Record {sr_id} is Receivable — La Poste CI can only execute outbound (Payable) settlements.", "error")
        return redirect(url_for("settlement"))

    if not pi_id:
        conn.close()
        flash("Payment Instruction ID is required.", "error")
        return redirect(url_for("settlement"))

    if not ta_id:
        conn.close()
        flash("Treasury Approval ID is required.", "error")
        return redirect(url_for("settlement"))

    # ── Determine amount ──────────────────────────────────────────────────────
    full_amount = record["Amount"]
    is_partial  = (stype == "partial")

    if is_partial:
        try:
            partial_amount = float(raw_partial)
        except (ValueError, TypeError):
            conn.close()
            flash("Partial amount must be a valid number.", "error")
            return redirect(url_for("settlement"))
        if partial_amount <= 0:
            conn.close()
            flash("Partial amount must be greater than 0.", "error")
            return redirect(url_for("settlement"))
        if partial_amount >= full_amount:
            conn.close()
            flash(f"Partial amount ({partial_amount:,.2f}) must be less than the full obligation ({full_amount:,.2f}). Use Full Settlement for the complete amount.", "error")
            return redirect(url_for("settlement"))
        execute_amount = partial_amount
    else:
        execute_amount = full_amount

    conn.close()

    # ── Determine wallets ─────────────────────────────────────────────────────
    # La Poste CI always sends from issuer_treasury for outbound settlements
    from_key = "issuer_treasury"
    to_key   = _COUNTERPARTY_WALLET_KEY.get(record["CounterpartyID"])
    if not to_key:
        flash(f"Unknown counterparty '{record['CounterpartyID']}'.", "error")
        return redirect(url_for("settlement"))

    if not XRPL_SERVICE_AVAILABLE:
        flash("XRPL service not available.", "error")
        return redirect(url_for("settlement"))

    # ── Submit to XRPL ────────────────────────────────────────────────────────
    result = xrpl_service.execute_settlement(
        from_operator_id       = from_key,
        to_operator_id         = to_key,
        amount                 = execute_amount,
        clearing_cycle_id      = record["ClearingCycleID"],
        payment_instruction_id = pi_id,
        treasury_approval_id   = ta_id,
        is_partial             = is_partial,
    )

    if not result["success"]:
        flash(f"XRPL settlement failed: {result['error']}", "error")
        return redirect(url_for("settlement"))

    tx_hash     = result["tx_hash"]
    now         = datetime.now(timezone.utc).isoformat()
    cycle_id    = record["ClearingCycleID"]
    cp_label    = _COUNTERPARTY_LABELS.get(record["CounterpartyID"], record["CounterpartyID"])
    tx_type     = "PartialSettlement" if is_partial else "Settlement"

    memo_json = json.dumps({
        "SettlementCycleID":    cycle_id,
        "PaymentInstructionID": pi_id,
        "TreasuryApprovalID":   ta_id,
        "TransactionType":      "Settlement",
        "PartialPayment":       "Y" if is_partial else "N",
    })

    # Load wallet addresses for the DB record
    try:
        _accts = json.load(open("accounts.json"))
        from_addr = _accts["issuer_treasury"]["address"]
        to_addr   = _accts[to_key]["address"]
    except Exception:
        from_addr = to_addr = ""

    conn = get_db()

    # Insert xrpl_transactions row
    conn.execute(
        """INSERT INTO xrpl_transactions
               (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
                Amount, MemoJSON, Status, ConfirmedAt)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Confirmed', ?)""",
        (tx_hash, cycle_id, tx_type, from_addr, to_addr,
         execute_amount, memo_json, now)
    )

    # Record execution metrics for the comparison panel
    conn.execute(
        """INSERT INTO settlement_metrics
               (ClearingCycleID, TxHash, TransactionType,
                AmountUSDST, ExecutionTimeSeconds, RecordedAt)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (cycle_id, tx_hash, tx_type,
         execute_amount, result.get("execution_seconds"), now)
    )

    if is_partial:
        # Mark original record Partially_Settled
        conn.execute(
            "UPDATE settlement_records SET Status='Partially_Settled', UpdatedAt=? WHERE SettlementRecordID=?",
            (now, sr_id)
        )
        # Create carry-forward record
        carry_amount = full_amount - execute_amount
        carry_id     = _gen_sr_id()
        conn.execute(
            """INSERT INTO settlement_records
                   (SettlementRecordID, ClearingCycleID, CounterpartyID,
                    Amount, Currency, Direction, Status,
                    PartialCarryForwardFlag, LinkedSettlementRecordID,
                    CreatedAt, UpdatedAt)
               VALUES (?, ?, ?, ?, 'USDST', 'Payable',
                       'Pending_Settlement', 'Y', ?, ?, ?)""",
            (carry_id, cycle_id, record["CounterpartyID"],
             carry_amount, sr_id, now, now)
        )
        conn.commit()
        conn.close()

        log_action(cycle_id, "Settlement_Executed_Partial",
                   f"Partial settlement of {execute_amount:,.2f} USDST to {cp_label} "
                   f"via {pi_id}. Remaining {carry_amount:,.2f} USDST carried forward "
                   f"as {carry_id}. TxHash: {tx_hash}")
        flash(
            f"Partial settlement of {execute_amount:,.2f} USDST executed to {cp_label}. "
            f"Carry-forward record {carry_id} created for {carry_amount:,.2f} USDST. "
            f"Tx: {tx_hash}",
            "success"
        )
    else:
        # Mark fully settled
        conn.execute(
            "UPDATE settlement_records SET Status='Settled', UpdatedAt=? WHERE SettlementRecordID=?",
            (now, sr_id)
        )
        conn.commit()
        conn.close()

        log_action(cycle_id, "Settlement_Executed_Full",
                   f"Full settlement of {execute_amount:,.2f} USDST to {cp_label} "
                   f"via {pi_id}. TxHash: {tx_hash}")
        flash(
            f"Settlement of {execute_amount:,.2f} USDST executed to {cp_label}. "
            f"Tx: {tx_hash}",
            "success"
        )

    return redirect(url_for("settlement"))


@app.route("/settlement/update-inbound-status", methods=["POST"])
def settlement_update_inbound():
    sr_id      = request.form.get("settlement_record_id", "").strip()
    new_status = request.form.get("new_status", "").strip()
    notes      = request.form.get("notes", "").strip()

    conn = get_db()
    record = conn.execute(
        "SELECT * FROM settlement_records WHERE SettlementRecordID = ?", (sr_id,)
    ).fetchone()

    if not record:
        conn.close()
        flash(f"Record '{sr_id}' not found.", "error")
        return redirect(url_for("settlement"))

    if record["Direction"] != "Receivable":
        conn.close()
        flash(f"Record {sr_id} is Payable — use the Execute Settlement form for outbound records.", "error")
        return redirect(url_for("settlement"))

    db_status = _INBOUND_STATUS_MAP.get(new_status)
    if not db_status:
        conn.close()
        flash(f"Invalid status '{new_status}'.", "error")
        return redirect(url_for("settlement"))

    old_status = record["Status"]
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "UPDATE settlement_records SET Status=?, UpdatedAt=? WHERE SettlementRecordID=?",
        (db_status, now, sr_id)
    )
    conn.commit()
    conn.close()

    details = (
        f"Record {sr_id}: status updated from {old_status} to {db_status} "
        f"(reported as: {new_status})"
    )
    if notes:
        details += f". Notes: {notes}"

    log_action(record["ClearingCycleID"], "Inbound_Status_Updated", details)
    flash(
        f"Inbound record {sr_id} updated to {new_status}.",
        "success"
    )
    return redirect(url_for("settlement"))


@app.route("/settlement/simulate-inbound", methods=["POST"])
def settlement_simulate_inbound():
    sr_id = request.form.get("settlement_record_id", "").strip()
    ta_id = request.form.get("treasury_approval_id", "").strip()

    conn = get_db()
    record = conn.execute(
        "SELECT * FROM settlement_records WHERE SettlementRecordID = ?", (sr_id,)
    ).fetchone()

    if not record:
        conn.close()
        flash(f"Settlement record '{sr_id}' not found.", "error")
        return redirect(url_for("settlement"))

    if record["Direction"] != "Receivable":
        conn.close()
        flash(f"Record {sr_id} is Payable — simulation only applies to Receivable records.", "error")
        return redirect(url_for("settlement"))

    if record["Status"] != "Pending_Settlement":
        conn.close()
        flash(f"Record {sr_id} is already '{record['Status']}' — simulation only applies to Pending_Settlement records.", "error")
        return redirect(url_for("settlement"))

    if not ta_id:
        conn.close()
        flash("Treasury Approval ID is required.", "error")
        return redirect(url_for("settlement"))

    conn.close()

    if not XRPL_SERVICE_AVAILABLE:
        flash("XRPL service not available.", "error")
        return redirect(url_for("settlement"))

    cp_id        = record["CounterpartyID"]
    from_operator = _COUNTERPARTY_WALLET_KEY.get(cp_id)
    if not from_operator:
        flash(f"Unknown counterparty '{cp_id}'.", "error")
        return redirect(url_for("settlement"))

    cp_label  = _COUNTERPARTY_LABELS.get(cp_id, cp_id)
    cycle_id  = record["ClearingCycleID"]
    amount    = record["Amount"]
    pi_id     = f"PI_{random.randint(10000, 99999)}"

    result = xrpl_service.simulate_inbound_payment(
        from_operator_id       = from_operator,
        amount                 = amount,
        clearing_cycle_id      = cycle_id,
        settlement_record_id   = sr_id,
        payment_instruction_id = pi_id,
        treasury_approval_id   = ta_id,
    )

    if not result["success"]:
        flash(f"Simulation failed: {result.get('error', 'Unknown error')}", "error")
        return redirect(url_for("settlement"))

    tx_hash  = result["tx_hash"]
    now      = datetime.now(timezone.utc).isoformat()

    memo_json = json.dumps({
        "SettlementCycleID":    cycle_id,
        "SettlementRecordID":   sr_id,
        "PaymentInstructionID": pi_id,
        "TreasuryApprovalID":   ta_id,
        "TransactionType":      "InboundSettlement",
        "SimulatedInbound":     "Y",
        "PartialPayment":       "N",
    })

    try:
        _accts    = json.load(open("accounts.json"))
        from_addr = _accts[from_operator]["address"]
        to_addr   = _accts["issuer_treasury"]["address"]
    except Exception:
        from_addr = to_addr = ""

    conn = get_db()
    conn.execute(
        """INSERT INTO xrpl_transactions
               (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
                Amount, MemoJSON, Status, ConfirmedAt)
           VALUES (?, ?, 'InboundSettlement', ?, ?, ?, ?, 'Confirmed', ?)""",
        (tx_hash, cycle_id, from_addr, to_addr, amount, memo_json, now)
    )
    conn.execute(
        """INSERT INTO settlement_metrics
               (ClearingCycleID, TxHash, TransactionType,
                AmountUSDST, ExecutionTimeSeconds, RecordedAt)
           VALUES (?, ?, 'InboundSettlement', ?, ?, ?)""",
        (cycle_id, tx_hash, amount, result.get("execution_seconds"), now)
    )
    conn.execute(
        "UPDATE settlement_records SET Status='Settled', UpdatedAt=? WHERE SettlementRecordID=?",
        (now, sr_id)
    )
    conn.commit()
    conn.close()

    log_action(cycle_id, "Inbound_Settlement_Simulated",
               f"Simulated inbound payment of {amount:,.2f} USDST from {cp_label} "
               f"via {pi_id}. TxHash: {tx_hash}")
    flash(
        f"Inbound simulation complete: {amount:,.2f} USDST received from {cp_label}. "
        f"Tx: {tx_hash}",
        "success"
    )
    return redirect(url_for("settlement"))


# ── Reconciliation & Exception Handling — Screen 6 ────────────────────────────

def _load_wallet_addresses() -> dict:
    """Return address map from accounts.json."""
    try:
        with open("accounts.json") as f:
            raw = json.load(f)
        return {
            "issuer_treasury": raw["issuer_treasury"]["address"],
            "operator_a":      raw["operator_a"]["address"],   # UAE Post
            "operator_b":      raw["operator_b"]["address"],   # Kenya Post
        }
    except Exception:
        return {}


def _expected_wallets(record: dict, addrs: dict) -> tuple:
    """
    Derive (expected_from, expected_to) for a settlement record.
    Payable  → La Poste CI (issuer_treasury) pays the counterparty.
    Receivable → counterparty pays La Poste CI (issuer_treasury).
    """
    cp_key = "operator_a" if record["CounterpartyID"] == "UAE_POST" else "operator_b"
    if record["Direction"] == "Payable":
        return addrs.get("issuer_treasury", ""), addrs.get(cp_key, "")
    else:
        return addrs.get(cp_key, ""), addrs.get("issuer_treasury", "")


def _reconciliation_context(last_tx: str = None) -> dict:
    """Gather all data needed by reconciliation.html."""
    conn = get_db()

    all_txs_raw = conn.execute(
        "SELECT * FROM xrpl_transactions ORDER BY ConfirmedAt DESC"
    ).fetchall()
    all_txs = [dict(r) for r in all_txs_raw]

    # Counts
    confirmed_count   = sum(1 for t in all_txs if t["Status"] == "Confirmed")
    reconciled_count  = sum(1 for t in all_txs if t["Status"] == "Reconciled")
    exception_count   = sum(1 for t in all_txs if t["Status"] == "Exception")

    # For each tx attach the linked settlement record (same cycle, best-match)
    # Build a lookup: cycle_id → list of settlement records
    sr_by_cycle: dict = {}
    for row in conn.execute("SELECT * FROM settlement_records").fetchall():
        r = dict(row)
        sr_by_cycle.setdefault(r["ClearingCycleID"], []).append(r)

    for tx in all_txs:
        cycle_records = sr_by_cycle.get(tx["ClearingCycleID"], [])
        # Find best-match: same TransactionType family and closest amount
        tx["LinkedRecord"] = None
        for sr in cycle_records:
            if abs((sr["Amount"] or 0) - (tx["Amount"] or 0)) < 0.01:
                tx["LinkedRecord"] = sr
                break

    # Pending reconciliation = settlement records Settled/Partially_Settled
    # but whose cycle has no Reconciled tx yet
    recon_tx_hashes = {t["TxHash"] for t in all_txs if t["Status"] == "Reconciled"}
    pending_sr = [
        dict(r) for r in conn.execute(
            """SELECT * FROM settlement_records
               WHERE Status IN ('Settled','Partially_Settled')
               ORDER BY UpdatedAt DESC"""
        ).fetchall()
    ]

    # Confirmed transactions (not yet reconciled/excepted) for the pending table
    pending_txs = [t for t in all_txs if t["Status"] == "Confirmed"]

    # Exception transactions
    exception_txs = [t for t in all_txs if t["Status"] == "Exception"]

    # Exception resolution entries from audit_log
    exception_resolutions = {}
    for row in conn.execute(
        """SELECT * FROM audit_log
           WHERE Action = 'Exception_Resolved' ORDER BY Timestamp DESC"""
    ).fetchall():
        r = dict(row)
        # Extract TxHash from Details (format: "... TxHash: XXX ...")
        for part in r["Details"].split():
            if len(part) == 64:   # XRPL tx hashes are 64 hex chars
                exception_resolutions[part] = r
                break

    # Last reconciliation result (for Section 4 display)
    last_result = None
    if last_tx:
        row = conn.execute(
            """SELECT * FROM audit_log
               WHERE (Action = 'Reconciliation_Confirmed'
                      OR Action = 'Reconciliation_Exception')
                 AND Details LIKE ?
               ORDER BY Timestamp DESC LIMIT 1""",
            (f"%{last_tx}%",)
        ).fetchone()
        if row:
            last_result = dict(row)

    conn.close()

    addrs = _load_wallet_addresses()

    return {
        "all_txs":             all_txs,
        "pending_txs":         pending_txs,
        "exception_txs":       exception_txs,
        "pending_sr":          pending_sr,
        "confirmed_count":     confirmed_count,
        "reconciled_count":    reconciled_count,
        "exception_count":     exception_count,
        "pending_count":       confirmed_count,
        "exception_resolutions": exception_resolutions,
        "last_result":         last_result,
        "last_tx":             last_tx,
        "addrs":               addrs,
    }


@app.route("/reconciliation")
def reconciliation():
    last_tx = request.args.get("last_tx")
    return render_template("reconciliation.html",
                           **_reconciliation_context(last_tx))


@app.route("/reconciliation/reconcile", methods=["POST"])
def reconciliation_reconcile():
    tx_hash = request.form.get("tx_hash", "").strip()
    sr_id   = request.form.get("settlement_record_id", "").strip()

    conn = get_db()
    tx_row = conn.execute(
        "SELECT * FROM xrpl_transactions WHERE TxHash = ?", (tx_hash,)
    ).fetchone()
    sr_row = conn.execute(
        "SELECT * FROM settlement_records WHERE SettlementRecordID = ?", (sr_id,)
    ).fetchone()
    conn.close()

    if not tx_row:
        flash(f"Transaction '{tx_hash[:16]}…' not found in database.", "error")
        return redirect(url_for("reconciliation"))
    if not sr_row:
        flash(f"Settlement record '{sr_id}' not found.", "error")
        return redirect(url_for("reconciliation"))

    addrs = _load_wallet_addresses()
    record = dict(sr_row)
    exp_from, exp_to = _expected_wallets(record, addrs)

    if not XRPL_SERVICE_AVAILABLE:
        flash("XRPL service not available.", "error")
        return redirect(url_for("reconciliation"))

    result = xrpl_service.reconcile_transaction(
        tx_hash          = tx_hash,
        expected_amount  = record["Amount"],
        expected_from    = exp_from,
        expected_to      = exp_to,
        expected_cycle_id = record["ClearingCycleID"],
    )

    now      = datetime.now(timezone.utc).isoformat()
    cycle_id = record["ClearingCycleID"]
    checks   = result["checks"]

    conn = get_db()
    if result["reconciled"]:
        conn.execute(
            "UPDATE xrpl_transactions SET Status='Reconciled', ReconciledAt=? WHERE TxHash=?",
            (now, tx_hash)
        )
        conn.execute(
            "UPDATE settlement_records SET Status='Reconciled', UpdatedAt=? WHERE SettlementRecordID=?",
            (now, sr_id)
        )
        conn.commit()
        conn.close()

        details = (
            f"TxHash: {tx_hash} | Cycle: {cycle_id} | "
            f"Amount: ✓ | Sender: ✓ | Receiver: ✓ | Memo: ✓"
        )
        log_action(cycle_id, "Reconciliation_Confirmed", details)
        flash("Transaction reconciled. All checks passed.", "success")
    else:
        conn.execute(
            "UPDATE xrpl_transactions SET Status='Exception' WHERE TxHash=?",
            (tx_hash,)
        )
        conn.commit()
        conn.close()

        reason = result.get("exception_reason", "UNKNOWN")
        chk    = checks
        details = (
            f"TxHash: {tx_hash} | Cycle: {cycle_id} | "
            f"Reason: {reason} | "
            f"Amount: {'✓' if chk['amount_match'] else '✗'} | "
            f"Sender: {'✓' if chk['sender_match'] else '✗'} | "
            f"Receiver: {'✓' if chk['receiver_match'] else '✗'} | "
            f"Memo: {'✓' if chk['memo_match'] else '✗'}"
        )
        log_action(cycle_id, "Reconciliation_Exception", details)
        flash(
            f"Reconciliation exception: {reason}. "
            f"Transaction flagged for manual review.",
            "error"
        )

    return redirect(url_for("reconciliation", last_tx=tx_hash))


@app.route("/reconciliation/resolve-exception", methods=["POST"])
def reconciliation_resolve_exception():
    tx_hash    = request.form.get("tx_hash", "").strip()
    action     = request.form.get("resolution_action", "").strip()
    notes      = request.form.get("resolution_notes", "").strip()

    conn = get_db()
    tx_row = conn.execute(
        "SELECT * FROM xrpl_transactions WHERE TxHash = ?", (tx_hash,)
    ).fetchone()
    conn.close()

    if not tx_row:
        flash(f"Transaction '{tx_hash[:16]}…' not found.", "error")
        return redirect(url_for("reconciliation"))

    valid_actions = ("correct_reference", "retry", "write_off")
    if action not in valid_actions:
        flash("Invalid resolution action.", "error")
        return redirect(url_for("reconciliation"))

    cycle_id = tx_row["ClearingCycleID"]
    now      = datetime.now(timezone.utc).isoformat()

    if action == "write_off":
        # Mark the linked settlement record as Exception
        conn = get_db()
        conn.execute(
            """UPDATE settlement_records
               SET Status='Exception', UpdatedAt=?
               WHERE ClearingCycleID=?
                 AND ABS(Amount - ?) < 0.01""",
            (now, cycle_id, tx_row["Amount"])
        )
        conn.commit()
        conn.close()

    label = {
        "correct_reference": "Correct Reference",
        "retry":             "Retry Payment",
        "write_off":         "Write Off",
    }[action]

    details = (
        f"TxHash: {tx_hash} | Action: {label}"
        + (f" | Notes: {notes}" if notes else "")
    )
    log_action(cycle_id, "Exception_Resolved", details)
    flash(f"Exception resolved: {label}.", "success")
    return redirect(url_for("reconciliation"))


@app.route("/reconciliation/reconcile-all", methods=["POST"])
def reconciliation_reconcile_all():
    """
    Auto-reconcile all Confirmed xrpl_transactions against their best-matching
    settlement record.  Returns JSON so the template can consume it via fetch().
    """
    if not XRPL_SERVICE_AVAILABLE:
        return jsonify({"error": "XRPL service not available"}), 503

    addrs = _load_wallet_addresses()

    conn = get_db()
    confirmed_txs = [dict(r) for r in conn.execute(
        "SELECT * FROM xrpl_transactions WHERE Status='Confirmed'"
    ).fetchall()]

    # All settlement records regardless of status — type-specific matching filters below
    sr_rows = conn.execute("SELECT * FROM settlement_records").fetchall()

    # Fetch active+closed cycles for Issuance verification
    cycle_ids = {r["ClearingCycleID"] for r in conn.execute(
        "SELECT ClearingCycleID FROM clearing_cycles"
    ).fetchall()}
    conn.close()

    sr_by_cycle: dict = {}
    for row in sr_rows:
        r = dict(row)
        sr_by_cycle.setdefault(r["ClearingCycleID"], []).append(r)

    total        = len(confirmed_txs)
    n_reconciled = 0
    n_exceptions = 0
    results      = []

    for tx in confirmed_txs:
        cycle_id = tx["ClearingCycleID"] or ""
        tx_type  = tx.get("TransactionType", "")
        tx_amt   = tx["Amount"] or 0
        now      = datetime.now(timezone.utc).isoformat()

        # ── Issuance / Redemption: verify against clearing cycle only ──────────
        if tx_type in ("Issuance", "Redemption", "TrustLine"):
            memo_json = {}
            try:
                memo_json = json.loads(tx.get("MemoJSON") or "{}")
            except Exception:
                pass
            memo_cycle = memo_json.get("SettlementCycleID", "")

            if cycle_id in cycle_ids and memo_cycle == cycle_id:
                conn = get_db()
                conn.execute(
                    "UPDATE xrpl_transactions SET Status='Reconciled', ReconciledAt=? WHERE TxHash=?",
                    (now, tx["TxHash"])
                )
                conn.commit()
                conn.close()
                log_action(cycle_id, "Reconciliation_Confirmed",
                           f"TxHash: {tx['TxHash']} | {tx_type} verified against cycle {cycle_id}")
                n_reconciled += 1
                results.append({"tx_hash": tx["TxHash"], "reconciled": True,
                                 "reason": "ISSUANCE_CYCLE_VERIFIED"})
            else:
                conn = get_db()
                conn.execute(
                    "UPDATE xrpl_transactions SET Status='Exception' WHERE TxHash=?",
                    (tx["TxHash"],)
                )
                conn.commit()
                conn.close()
                log_action(cycle_id, "Reconciliation_Exception",
                           f"TxHash: {tx['TxHash']} | {tx_type} cycle mismatch: memo={memo_cycle}")
                n_exceptions += 1
                results.append({"tx_hash": tx["TxHash"], "reconciled": False,
                                 "reason": "ISSUANCE_CYCLE_MISMATCH"})
            continue

        # ── Settlement / PartialSettlement: match against settlement_records ───
        pool = sr_by_cycle.get(cycle_id, [])

        if tx_type == "PartialSettlement":
            # Match the original record that was partially settled.
            # SR.Amount >= tx amount (original obligation was larger than what was paid).
            candidates = [
                sr for sr in pool
                if sr["Status"] == "Partially_Settled"
                and (sr["Amount"] or 0) >= tx_amt - 0.01
            ]
            # Take closest (smallest difference) so a 5000 tx matches 8500 SR, not a larger one
            candidates.sort(key=lambda r: (r["Amount"] or 0))
            best_sr = candidates[0] if candidates else None
            # Use the actual tx amount as expected_amount — the SR amount is the original full amount
            expected_amount_for_check = tx_amt

        else:  # Settlement
            candidates = [
                sr for sr in pool
                if sr["Status"] in ("Settled", "Pending_Settlement")
                and abs((sr["Amount"] or 0) - tx_amt) < 0.01
            ]
            best_sr = candidates[0] if candidates else None
            expected_amount_for_check = best_sr["Amount"] if best_sr else tx_amt

        if not best_sr:
            # No matching record — flag as exception
            now = datetime.now(timezone.utc).isoformat()
            conn = get_db()
            conn.execute(
                "UPDATE xrpl_transactions SET Status='Exception' WHERE TxHash=?",
                (tx["TxHash"],)
            )
            conn.commit()
            conn.close()
            log_action(cycle_id, "Reconciliation_Exception",
                       f"TxHash: {tx['TxHash']} | No matching settlement record found.")
            n_exceptions += 1
            results.append({
                "tx_hash":    tx["TxHash"],
                "reconciled": False,
                "reason":     "NO_MATCHING_RECORD",
            })
            continue

        exp_from, exp_to = _expected_wallets(best_sr, addrs)
        recon = xrpl_service.reconcile_transaction(
            tx_hash           = tx["TxHash"],
            expected_amount   = expected_amount_for_check,
            expected_from     = exp_from,
            expected_to       = exp_to,
            expected_cycle_id = cycle_id,
        )

        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        if recon["reconciled"]:
            conn.execute(
                "UPDATE xrpl_transactions SET Status='Reconciled', ReconciledAt=? WHERE TxHash=?",
                (now, tx["TxHash"])
            )
            conn.execute(
                "UPDATE settlement_records SET Status='Reconciled', UpdatedAt=? WHERE SettlementRecordID=?",
                (now, best_sr["SettlementRecordID"])
            )
            conn.commit()
            conn.close()
            log_action(cycle_id, "Reconciliation_Confirmed",
                       f"TxHash: {tx['TxHash']} | Auto-reconciled against {best_sr['SettlementRecordID']}")
            n_reconciled += 1
            results.append({
                "tx_hash":    tx["TxHash"],
                "reconciled": True,
                "sr_id":      best_sr["SettlementRecordID"],
            })
        else:
            conn.execute(
                "UPDATE xrpl_transactions SET Status='Exception' WHERE TxHash=?",
                (tx["TxHash"],)
            )
            conn.commit()
            conn.close()
            reason = recon.get("exception_reason", "UNKNOWN")
            log_action(cycle_id, "Reconciliation_Exception",
                       f"TxHash: {tx['TxHash']} | Reason: {reason}")
            n_exceptions += 1
            results.append({
                "tx_hash":    tx["TxHash"],
                "reconciled": False,
                "reason":     reason,
            })

    return jsonify({
        "total":       total,
        "reconciled":  n_reconciled,
        "exceptions":  n_exceptions,
        "results":     results,
    })

# ── Audit Log helpers ──────────────────────────────────────────────────────────

def _audit_report_context(cycle_id: str):
    """
    Build the full settlement report dict for one clearing cycle.
    Returns None if the cycle does not exist in the database.
    Covers all data needed by Section 3 of audit.html and the CSV exports.
    """
    conn = get_db()
    cycle_row = conn.execute(
        "SELECT * FROM clearing_cycles WHERE ClearingCycleID=?", (cycle_id,)
    ).fetchone()
    if not cycle_row:
        conn.close()
        return None
    cycle = dict(cycle_row)

    auths   = [dict(r) for r in conn.execute(
        "SELECT * FROM issuance_authorizations WHERE ClearingCycleID=? ORDER BY CreatedAt",
        (cycle_id,)
    ).fetchall()]
    sr_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM settlement_records WHERE ClearingCycleID=? ORDER BY CreatedAt",
        (cycle_id,)
    ).fetchall()]
    tx_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM xrpl_transactions WHERE ClearingCycleID=? ORDER BY ConfirmedAt",
        (cycle_id,)
    ).fetchall()]
    audit_count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE ClearingCycleID=?", (cycle_id,)
    ).fetchone()[0]
    conn.close()

    # ── Token economics ────────────────────────────────────────────────────────
    usd_backing    = cycle.get("USDBackingAmount") or 0.0
    usdst_issued   = cycle.get("USDSTIssued")      or 0.0
    usdst_redeemed = sum(
        (tx["Amount"] or 0) for tx in tx_rows if tx["TransactionType"] == "Redemption"
    )
    outstanding = max(0.0, usdst_issued - usdst_redeemed)

    # ── Obligations analysis ───────────────────────────────────────────────────
    total_obs        = len(sr_rows)
    settled_obs      = sum(1 for r in sr_rows if r["Status"] in ("Settled", "Reconciled"))
    partial_obs      = sum(1 for r in sr_rows if r["Status"] == "Partially_Settled")
    pending_obs      = sum(1 for r in sr_rows if r["Status"] == "Pending_Settlement")
    carry_fwd_count  = sum(1 for r in sr_rows if r["PartialCarryForwardFlag"] == "Y")
    settle_pct       = round((settled_obs / total_obs * 100) if total_obs else 0.0, 1)
    partial_freq     = round((carry_fwd_count / total_obs * 100) if total_obs else 0.0, 1)

    # ── XRPL stats ─────────────────────────────────────────────────────────────
    reconciled_count = sum(1 for tx in tx_rows if tx["Status"] == "Reconciled")
    exceptions_count = sum(1 for tx in tx_rows if tx["Status"] == "Exception")

    return {
        "cycle_id":             cycle_id,
        "generated_at":         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "cycle_status":         cycle["Status"],
        "usd_backing_confirmed": usd_backing,
        "usdst_issued":         usdst_issued,
        "usdst_redeemed":       usdst_redeemed,
        "outstanding_supply":   outstanding,
        "obligations": [
            {
                "record_id":   r["SettlementRecordID"],
                "counterparty": _COUNTERPARTY_LABELS.get(r["CounterpartyID"], r["CounterpartyID"]),
                "amount":      r["Amount"],
                "direction":   r["Direction"],
                "status":      r["Status"],
                "partial_flag": r["PartialCarryForwardFlag"],
            }
            for r in sr_rows
        ],
        "total_obligations":            total_obs,
        "settled_obligations":          settled_obs,
        "partially_settled":            partial_obs,
        "pending_obligations":          pending_obs,
        "settlement_percentage":        settle_pct,
        "partial_settlement_frequency": partial_freq,
        "xrpl_transactions": [
            {
                "tx_hash":      tx["TxHash"],
                "type":         tx["TransactionType"],
                "amount":       tx["Amount"],
                "status":       tx["Status"],
                "confirmed_at": tx["ConfirmedAt"],
            }
            for tx in tx_rows
        ],
        "exceptions_count":  exceptions_count,
        "reconciled_count":  reconciled_count,
        "authorizations": [
            {
                "auth_id": a["IssuanceAuthorizationID"],
                "maximum": a["MaximumIssuableAmount"],
                "issued":  a["AmountIssued"],
                "status":  a["Status"],
            }
            for a in auths
        ],
        "audit_entries": audit_count,
    }


# ── Audit Log routes ───────────────────────────────────────────────────────────

@app.route("/audit")
def audit_log_view():
    """
    Route 1: GET /audit
    Main audit log page. Shows activity summary cards, an optional per-cycle
    settlement report panel (Section 3), the paginated audit trail table
    (Section 4), and the full XRPL transaction ledger (Section 5) when a
    cycle is selected.
    """
    cycle_filter = request.args.get("cycle", "").strip()

    conn = get_db()
    cycles = [dict(r) for r in conn.execute(
        "SELECT ClearingCycleID, Status FROM clearing_cycles ORDER BY CreatedAt DESC"
    ).fetchall()]

    # Audit entries — cap at 500 for client-side pagination performance
    if cycle_filter:
        entries = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE ClearingCycleID=? ORDER BY Timestamp DESC LIMIT 500",
            (cycle_filter,)
        ).fetchall()]
    else:
        entries = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY Timestamp DESC LIMIT 500"
        ).fetchall()]

    # Summary statistics
    total_actions = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    today_prefix  = datetime.now(timezone.utc).date().isoformat()
    actions_today = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE Timestamp LIKE ?",
        (today_prefix + "%",)
    ).fetchone()[0]
    cycles_tracked = conn.execute(
        "SELECT COUNT(DISTINCT ClearingCycleID) FROM audit_log "
        "WHERE ClearingCycleID IS NOT NULL AND ClearingCycleID != ''"
    ).fetchone()[0]
    exceptions_logged = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE Action LIKE '%Exception%'"
    ).fetchone()[0]
    conn.close()

    report    = _audit_report_context(cycle_filter) if cycle_filter else None
    cycle_txs = report["xrpl_transactions"] if report else []

    return render_template(
        "audit.html",
        entries       = entries,
        cycles        = cycles,
        cycle_filter  = cycle_filter,
        report        = report,
        cycle_txs     = cycle_txs,
        stats         = {
            "total_actions":     total_actions,
            "actions_today":     actions_today,
            "cycles_tracked":    cycles_tracked,
            "exceptions_logged": exceptions_logged,
        },
        explorer_base = EXPLORER_BASE,
        report_only   = False,
    )


@app.route("/audit/report")
def audit_report():
    """
    Route 2: GET /audit/report?cycle=
    Report-focused view of audit.html: shows the settlement report panel and
    the transaction ledger but hides the activity cards and audit trail table,
    giving a clean printable report for a single cycle.
    """
    cycle_filter = request.args.get("cycle", "").strip()
    if not cycle_filter:
        flash("Select a cycle to view its report.", "info")
        return redirect(url_for("audit_log_view"))

    report = _audit_report_context(cycle_filter)
    if not report:
        flash(f"Cycle '{cycle_filter}' not found.", "error")
        return redirect(url_for("audit_log_view"))

    conn = get_db()
    cycles = [dict(r) for r in conn.execute(
        "SELECT ClearingCycleID, Status FROM clearing_cycles ORDER BY CreatedAt DESC"
    ).fetchall()]
    conn.close()

    return render_template(
        "audit.html",
        entries       = [],
        cycles        = cycles,
        cycle_filter  = cycle_filter,
        report        = report,
        cycle_txs     = report["xrpl_transactions"],
        stats         = {"total_actions": 0, "actions_today": 0,
                         "cycles_tracked": 0, "exceptions_logged": 0},
        explorer_base = EXPLORER_BASE,
        report_only   = True,
    )


@app.route("/audit/export/csv")
def audit_export_csv():
    """
    Route 3: GET /audit/export/csv?cycle=
    Streams a CSV download of audit_log entries. When ?cycle= is provided
    the export is scoped to that cycle; otherwise the full log is exported.
    A TxHash column is extracted from the Details field where present.
    Filename: postal_settlement_audit_[cycle]_[date].csv
    """
    cycle_filter = request.args.get("cycle", "").strip()
    conn = get_db()
    if cycle_filter:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE ClearingCycleID=? ORDER BY Timestamp DESC",
            (cycle_filter,)
        ).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY Timestamp DESC"
        ).fetchall()]
    conn.close()

    tx_re = re.compile(r"TxHash:\s*([A-F0-9]{16,})", re.IGNORECASE)

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["Timestamp", "ClearingCycleID", "Action", "Details", "Actor", "TxHash"])
    for e in rows:
        m       = tx_re.search(e.get("Details") or "")
        tx_hash = m.group(1) if m else ""
        w.writerow([
            e.get("Timestamp", ""),
            e.get("ClearingCycleID", ""),
            e.get("Action", ""),
            e.get("Details", ""),
            e.get("Actor", ""),
            tx_hash,
        ])

    today      = datetime.now(timezone.utc).date().isoformat()
    cycle_part = cycle_filter.replace("/", "-") if cycle_filter else "all"
    filename   = f"postal_settlement_audit_{cycle_part}_{today}.csv"

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/audit/export/full-report-csv")
def audit_export_full_csv():
    """
    Route 4: GET /audit/export/full-report-csv?cycle=
    Generates a multi-section CSV containing:
      Section 1 — Cycle summary key/value pairs
      Section 2 — Settlement obligations (one row per SR)
      Section 3 — XRPL transactions (one row per tx)
      Section 4 — Full audit trail for the cycle
    Sections are separated by a blank row and a SECTION header row.
    Filename: postal_settlement_full_report_[cycle]_[date].csv
    """
    cycle_filter = request.args.get("cycle", "").strip()
    if not cycle_filter:
        flash("Select a cycle to export the full report.", "error")
        return redirect(url_for("audit_log_view"))

    report = _audit_report_context(cycle_filter)
    if not report:
        flash(f"Cycle '{cycle_filter}' not found.", "error")
        return redirect(url_for("audit_log_view"))

    conn = get_db()
    audit_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log WHERE ClearingCycleID=? ORDER BY Timestamp ASC",
        (cycle_filter,)
    ).fetchall()]
    conn.close()

    buf = io.StringIO()
    w   = csv.writer(buf)

    # ── Section 1: Cycle Summary ───────────────────────────────────────────────
    w.writerow(["SECTION", "Cycle Summary"])
    w.writerow(["Field", "Value"])
    w.writerow(["Cycle ID",                    report["cycle_id"]])
    w.writerow(["Status",                      report["cycle_status"]])
    w.writerow(["Generated At",                report["generated_at"]])
    w.writerow(["USD Backing Confirmed",        f"${report['usd_backing_confirmed']:,.2f}"])
    w.writerow(["USDST Issued",                f"{report['usdst_issued']:,.2f}"])
    w.writerow(["USDST Redeemed",              f"{report['usdst_redeemed']:,.2f}"])
    w.writerow(["Outstanding Supply",          f"{report['outstanding_supply']:,.2f}"])
    w.writerow(["Settlement %",                f"{report['settlement_percentage']}%"])
    w.writerow(["Partial Settlement Frequency", f"{report['partial_settlement_frequency']}%"])
    w.writerow(["Exceptions",                  report["exceptions_count"]])
    w.writerow(["Reconciled Transactions",     report["reconciled_count"]])
    w.writerow([])

    # ── Section 2: Settlement Obligations ─────────────────────────────────────
    w.writerow(["SECTION", "Settlement Obligations"])
    w.writerow(["Record ID", "Counterparty", "Direction", "Amount (USDST)",
                "Status", "Carry-Forward"])
    for ob in report["obligations"]:
        w.writerow([ob["record_id"], ob["counterparty"], ob["direction"],
                    ob["amount"], ob["status"], ob["partial_flag"]])
    w.writerow([])

    # ── Section 3: XRPL Transactions ──────────────────────────────────────────
    w.writerow(["SECTION", "XRPL Transactions"])
    w.writerow(["TxHash", "Type", "Amount (USDST)", "Status", "Confirmed At"])
    for tx in report["xrpl_transactions"]:
        w.writerow([tx["tx_hash"], tx["type"], tx["amount"],
                    tx["status"], tx["confirmed_at"]])
    w.writerow([])

    # ── Section 4: Audit Trail ─────────────────────────────────────────────────
    w.writerow(["SECTION", "Audit Trail"])
    w.writerow(["Timestamp", "Action", "Details", "Actor"])
    for e in audit_rows:
        w.writerow([e.get("Timestamp", ""), e.get("Action", ""),
                    e.get("Details", ""), e.get("Actor", "")])

    today      = datetime.now(timezone.utc).date().isoformat()
    cycle_part = cycle_filter.replace("/", "-")
    filename   = f"postal_settlement_full_report_{cycle_part}_{today}.csv"

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ── Comparison Panel API ───────────────────────────────────────────────────────

@app.route("/api/recent-settlements")
def api_recent_settlements():
    """
    Called by the comparison panel JS every 30 seconds to refresh the
    live ticker and update the metrics.  Always returns valid JSON.
    """
    rows = _recent_settlements(limit=5)
    settlements = [
        {
            "tx_hash":           r["tx_hash_short"],
            "amount":            r["AmountUSDST"],
            "execution_seconds": r["ExecutionTimeSeconds"],
            "recorded_at":       r["RecordedAt"],
            "time_ago":          r["time_ago"],
        }
        for r in rows
    ]

    conn = get_db()
    active = conn.execute(
        "SELECT ClearingCycleID FROM clearing_cycles "
        "WHERE Status='Active' ORDER BY CreatedAt DESC LIMIT 1"
    ).fetchone()
    conn.close()

    metrics = (
        calculate_cycle_metrics(active["ClearingCycleID"]) if active else None
    )

    return jsonify({"settlements": settlements, "comparison_metrics": metrics})


# ── Original XRPL step endpoints (preserved unchanged) ────────────────────────

def _run(fn, *args):
    accounts = load_accounts()
    with WebsocketClient(TESTNET_URL) as client:
        return fn(client, accounts, *args)


def _ok(tx_hash: str, label: str) -> dict:
    return {
        "status":   "confirmed",
        "label":    label,
        "hash":     tx_hash,
        "explorer": EXPLORER_BASE + tx_hash,
    }


def _err(exc: Exception) -> tuple:
    return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/step1", methods=["POST"])
def api_step1():
    if not XRPL_AVAILABLE:
        return _err(RuntimeError("xrpl-py not installed"))
    try:
        tx_hash = _run(step1_trustline_setup)
        return jsonify(_ok(tx_hash, "TrustLines active for UAE Post and Kenya Post"))
    except Exception as exc:
        traceback.print_exc()
        return _err(exc)


@app.route("/api/step2", methods=["POST"])
def api_step2():
    if not XRPL_AVAILABLE:
        return _err(RuntimeError("xrpl-py not installed"))
    try:
        tx_hash = _run(step2_issue)
        return jsonify(_ok(tx_hash, "15,300 USDST issued to UAE Post"))
    except Exception as exc:
        traceback.print_exc()
        return _err(exc)


@app.route("/api/step3", methods=["POST"])
def api_step3():
    if not XRPL_AVAILABLE:
        return _err(RuntimeError("xrpl-py not installed"))
    try:
        tx_hash = _run(step3_settlement)
        return jsonify(_ok(tx_hash, "15,300 USDST settled: UAE Post → Kenya Post"))
    except Exception as exc:
        traceback.print_exc()
        return _err(exc)


@app.route("/api/step4", methods=["POST"])
def api_step4():
    if not XRPL_AVAILABLE:
        return _err(RuntimeError("xrpl-py not installed"))
    try:
        tx_hash = _run(step4_partial)
        return jsonify(_ok(tx_hash, "10,000 USDST settled  |  5,300 USDST carry-forward recorded"))
    except Exception as exc:
        traceback.print_exc()
        return _err(exc)


@app.route("/api/step5", methods=["POST"])
def api_step5():
    if not XRPL_AVAILABLE:
        return _err(RuntimeError("xrpl-py not installed"))
    try:
        tx_hash, balance = _run(step5_redeem)
        return jsonify(_ok(tx_hash, f"{balance} USDST redeemed and burned"))
    except Exception as exc:
        traceback.print_exc()
        return _err(exc)


if __name__ == "__main__":
    app.run(debug=True)
