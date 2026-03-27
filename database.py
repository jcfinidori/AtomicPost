"""
database.py — POSTAL SETTLEMENT POC
SQLite schema, connection helper, and audit log writer.
"""

import sqlite3
from datetime import datetime, timezone

DB_PATH = "postal.db"


# ── Connection ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they do not already exist."""
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS clearing_cycles (
            ClearingCycleID     TEXT    PRIMARY KEY,
            CreatedAt           TIMESTAMP,
            Status              TEXT    CHECK(Status IN ('Active','Closed')),
            USDBackingAmount    REAL    DEFAULT 0,
            USDSTIssued         REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS issuance_authorizations (
            IssuanceAuthorizationID TEXT PRIMARY KEY,
            ClearingCycleID         TEXT,
            MaximumIssuableAmount   REAL,
            AmountIssued            REAL    DEFAULT 0,
            Status                  TEXT    CHECK(Status IN ('Active','Expired','FullyUtilized')),
            CreatedAt               TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settlement_records (
            SettlementRecordID      TEXT    PRIMARY KEY,
            ClearingCycleID         TEXT,
            CounterpartyID          TEXT    CHECK(CounterpartyID IN ('UAE_POST','KENYA_POST')),
            Amount                  REAL,
            Currency                TEXT    DEFAULT 'USDST',
            Direction               TEXT    CHECK(Direction IN ('Payable','Receivable')),
            Status                  TEXT    CHECK(Status IN (
                                        'Pending_Settlement','Settled',
                                        'Partially_Settled','Reconciled','Exception'
                                    )),
            PartialCarryForwardFlag TEXT    DEFAULT 'N' CHECK(PartialCarryForwardFlag IN ('Y','N')),
            LinkedSettlementRecordID TEXT,
            CreatedAt               TIMESTAMP,
            UpdatedAt               TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS xrpl_transactions (
            TxHash              TEXT    PRIMARY KEY,
            ClearingCycleID     TEXT,
            TransactionType     TEXT    CHECK(TransactionType IN (
                                    'TrustLine','Issuance','Settlement',
                                    'PartialSettlement','Redemption'
                                )),
            FromWallet          TEXT,
            ToWallet            TEXT,
            Amount              REAL,
            MemoJSON            TEXT,
            Status              TEXT    CHECK(Status IN ('Confirmed','Exception')),
            ConfirmedAt         TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            LogID           INTEGER PRIMARY KEY AUTOINCREMENT,
            ClearingCycleID TEXT,
            Action          TEXT,
            Details         TEXT,
            Timestamp       TIMESTAMP,
            Actor           TEXT DEFAULT 'Treasury Manager'
        );

        CREATE TABLE IF NOT EXISTS settlement_metrics (
            MetricID             INTEGER   PRIMARY KEY AUTOINCREMENT,
            ClearingCycleID      TEXT,
            TxHash               TEXT,
            TransactionType      TEXT,
            AmountUSDST          REAL,
            ExecutionTimeSeconds REAL,
            RecordedAt           TIMESTAMP
        );
    """)

    conn.commit()

    # ── Migration: expand xrpl_transactions.Status to include Reconciled ───────
    _migrate_xrpl_tx_status(conn)

    # ── Migration: expand TransactionType to include InboundSettlement ──────────
    _migrate_xrpl_tx_type(conn)

    conn.close()


def _migrate_xrpl_tx_status(conn: sqlite3.Connection):
    """
    One-time migration: add ReconciledAt column and expand the Status
    CHECK constraint on xrpl_transactions to include 'Reconciled'.
    Safe to call on every startup — skips if already migrated.
    """
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='xrpl_transactions'"
    ).fetchone()
    if not ddl_row or 'Reconciled' in ddl_row[0]:
        return   # already migrated or table doesn't exist yet

    conn.executescript("""
        BEGIN;

        ALTER TABLE xrpl_transactions RENAME TO xrpl_transactions_v1;

        CREATE TABLE xrpl_transactions (
            TxHash              TEXT    PRIMARY KEY,
            ClearingCycleID     TEXT,
            TransactionType     TEXT    CHECK(TransactionType IN (
                                    'TrustLine','Issuance','Settlement',
                                    'PartialSettlement','Redemption'
                                )),
            FromWallet          TEXT,
            ToWallet            TEXT,
            Amount              REAL,
            MemoJSON            TEXT,
            Status              TEXT    CHECK(Status IN ('Confirmed','Exception','Reconciled')),
            ConfirmedAt         TIMESTAMP,
            ReconciledAt        TIMESTAMP
        );

        INSERT INTO xrpl_transactions
            (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
             Amount, MemoJSON, Status, ConfirmedAt)
        SELECT TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
               Amount, MemoJSON, Status, ConfirmedAt
        FROM xrpl_transactions_v1;

        DROP TABLE xrpl_transactions_v1;

        COMMIT;
    """)


def _migrate_xrpl_tx_type(conn: sqlite3.Connection):
    """
    One-time migration: expand the TransactionType CHECK constraint on
    xrpl_transactions to include 'InboundSettlement'.
    Safe to call on every startup — skips if already migrated.
    """
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='xrpl_transactions'"
    ).fetchone()
    if not ddl_row or 'InboundSettlement' in ddl_row[0]:
        return   # already migrated or table doesn't exist yet

    conn.executescript("""
        BEGIN;

        ALTER TABLE xrpl_transactions RENAME TO xrpl_transactions_v2;

        CREATE TABLE xrpl_transactions (
            TxHash              TEXT    PRIMARY KEY,
            ClearingCycleID     TEXT,
            TransactionType     TEXT    CHECK(TransactionType IN (
                                    'TrustLine','Issuance','Settlement',
                                    'PartialSettlement','Redemption',
                                    'InboundSettlement'
                                )),
            FromWallet          TEXT,
            ToWallet            TEXT,
            Amount              REAL,
            MemoJSON            TEXT,
            Status              TEXT    CHECK(Status IN ('Confirmed','Exception','Reconciled')),
            ConfirmedAt         TIMESTAMP,
            ReconciledAt        TIMESTAMP
        );

        INSERT INTO xrpl_transactions
            (TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
             Amount, MemoJSON, Status, ConfirmedAt, ReconciledAt)
        SELECT TxHash, ClearingCycleID, TransactionType, FromWallet, ToWallet,
               Amount, MemoJSON, Status, ConfirmedAt, ReconciledAt
        FROM xrpl_transactions_v2;

        DROP TABLE xrpl_transactions_v2;

        COMMIT;
    """)


# ── Helpers ────────────────────────────────────────────────────────────────────

def calculate_cycle_metrics(clearing_cycle_id: str) -> dict:
    """
    Aggregate settlement_metrics rows for one cycle and produce the
    comparison dict consumed by comparison_panel.html.

    Returns a dict whether or not any transactions have been recorded yet.
    Zero-value fields mean "no data yet" rather than an error.
    """
    conn = get_db()

    met_rows = conn.execute(
        "SELECT AmountUSDST, ExecutionTimeSeconds FROM settlement_metrics "
        "WHERE ClearingCycleID = ?",
        (clearing_cycle_id,)
    ).fetchall()

    # Settlement records (original obligations only, no carry-forwards) for %
    sr_rows = conn.execute(
        "SELECT Status FROM settlement_records "
        "WHERE ClearingCycleID = ? AND PartialCarryForwardFlag = 'N'",
        (clearing_cycle_id,)
    ).fetchall()

    conn.close()

    total_transactions = len(met_rows)
    total_settled      = sum(r["AmountUSDST"] or 0 for r in met_rows)

    exec_times = [r["ExecutionTimeSeconds"] for r in met_rows
                  if r["ExecutionTimeSeconds"] is not None]
    avg_execution  = round(sum(exec_times) / len(exec_times), 2) if exec_times else None
    fastest        = round(min(exec_times), 2) if exec_times else None

    total_fees     = round(total_transactions * 0.000012, 8)
    trad_fees      = round(total_settled * 0.005 + total_transactions * 75, 2)

    total_obs   = len(sr_rows)
    settled_obs = sum(
        1 for r in sr_rows if r["Status"] in ("Settled", "Reconciled", "Partially_Settled")
    )
    settle_pct = round((settled_obs / total_obs * 100) if total_obs else 0.0, 1)

    return {
        "cycle_id":                    clearing_cycle_id,
        "total_settled":               total_settled,
        "total_transactions":          total_transactions,
        "avg_execution_seconds":       avg_execution,
        "fastest_settlement":          fastest,
        "total_fees_usdst":            total_fees,
        "traditional_estimated_days":  21,
        "traditional_estimated_fees":  trad_fees,
        "fx_exposure_days_avoided":    21,
        "settlement_percentage":       settle_pct,
    }


def log_action(cycle_id: str, action: str, details: str, actor: str = "Treasury Manager"):
    """Append one row to audit_log."""
    conn = get_db()
    conn.execute(
        """INSERT INTO audit_log (ClearingCycleID, Action, Details, Timestamp, Actor)
           VALUES (?, ?, ?, ?, ?)""",
        (cycle_id, action, details, datetime.now(timezone.utc).isoformat(), actor),
    )
    conn.commit()
    conn.close()
