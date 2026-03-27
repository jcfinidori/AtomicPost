"""
reset_demo.py — Postal Settlement POC
Wipes all transactional data and loads a clean demo scenario.
Safe to run multiple times.
"""

from datetime import datetime, timezone
from database import get_db

NOW = datetime.now(timezone.utc).isoformat()


def reset():
    conn = get_db()

    # ── 1. Clear all tables ────────────────────────────────────────────────────
    conn.executescript("""
        DELETE FROM audit_log;
        DELETE FROM xrpl_transactions;
        DELETE FROM settlement_records;
        DELETE FROM issuance_authorizations;
        DELETE FROM clearing_cycles;
    """)

    # ── 2. Insert clean starting scenario ─────────────────────────────────────
    conn.execute("""
        INSERT INTO clearing_cycles
            (ClearingCycleID, CreatedAt, Status, USDBackingAmount, USDSTIssued)
        VALUES (?, ?, 'Active', 0, 0)
    """, ("CYCLE_2026-03-20", NOW))

    conn.execute("""
        INSERT INTO settlement_records
            (SettlementRecordID, ClearingCycleID, CounterpartyID,
             Amount, Currency, Direction, Status,
             PartialCarryForwardFlag, CreatedAt, UpdatedAt)
        VALUES (?, ?, 'UAE_POST', 23500, 'USDST', 'Payable', 'Pending_Settlement', 'N', ?, ?)
    """, ("SR_UAE_001", "CYCLE_2026-03-20", NOW, NOW))

    conn.execute("""
        INSERT INTO settlement_records
            (SettlementRecordID, ClearingCycleID, CounterpartyID,
             Amount, Currency, Direction, Status,
             PartialCarryForwardFlag, CreatedAt, UpdatedAt)
        VALUES (?, ?, 'KENYA_POST', 8200, 'USDST', 'Receivable', 'Pending_Settlement', 'N', ?, ?)
    """, ("SR_KEN_001", "CYCLE_2026-03-20", NOW, NOW))

    conn.commit()
    conn.close()

    print("Demo reset complete.")
    print("Clean scenario loaded for CYCLE_2026-03-20")


if __name__ == "__main__":
    reset()
