# PostalSettle — Demo Walkthrough
### Hackathon Judge Guide · 10-minute evaluation

---

## What This Demo Shows

PostalSettle replaces correspondent banking for postal money order settlement
using blockchain-native stablecoins. Three postal operators settle real
cross-border obligations on XRPL Testnet and Solana Devnet in seconds instead
of weeks. Every step — from treasury authorization to reconciliation — is
auditable on-chain.

---

## The Scenario

**Corridor:** Ivory Coast → UAE → Kenya

| Operator       | Obligation          | Direction |
|----------------|---------------------|-----------|
| La Poste CI    | Owes $23,500        | Outbound to UAE Post |
| Kenya Post     | Owes $8,200         | Inbound to La Poste CI |

- **Traditional settlement:** 7–30 days, 2–3 correspondent bank hops, 5–7% cost
- **PostalSettle:** Under 10 minutes on testnet, zero intermediaries, \<0.01% cost

La Poste CI acts as the treasury hub: it confirms USD backing, mints USD-ST
stablecoins, settles obligations on-chain, and burns tokens when done.

---

## Quick Start

```bash
git clone https://github.com/jcfinidori/AtomicPost.git
cd AtomicPost
pip install -r requirements.txt
cp accounts_template.json accounts.json
python3 reset_demo.py
python3 app.py
```

Open **http://localhost:5000**

> `reset_demo.py` loads a clean scenario with the two obligations above.
> XRPL credentials in `accounts.json` are required for live transactions;
> the UI degrades gracefully without them.

---

## Screen by Screen

**Screen 1 — Dashboard**
The landing page shows the active clearing cycle, outstanding obligations, and
current USD-ST issuance against the reserve cap. This is the treasury manager's
single view of the entire settlement position. Note the live XRPL trustline
status indicators in the top panel.

**Screen 2 — Clearing Setup**
This is where net obligations are entered — in production these would be
calculated automatically from parcel CSV data. The two obligations for this
cycle (UAE Post $23,500 payable, Kenya Post $8,200 receivable) are pre-loaded
by `reset_demo.py`. The net settlement figure drives everything downstream.

**Screen 3 — Reserve Governance**
The treasury manager confirms how much USD is held in reserve and authorizes
a maximum issuable amount of USD-ST. This is the fiat-backing gate: no
stablecoins can be minted without an active authorization on this screen.
It proves the hard-cap enforcement that regulators require.

**Screen 4 — Issuance**
With authorization in place, the treasury mints USD-ST directly to operator
wallets via a real XRPL transaction. Each issuance references the authorization
ID in its on-chain memo for full traceability. The transaction hash and a link
to the XRPL explorer appear immediately on confirmation.

**Screen 5 — Settlement**
Operators execute net settlement payments on XRPL. Try the **partial settlement**
flow: enter an amount less than the full obligation and submit — the system
automatically creates a carry-forward record for the remainder. This replicates
real-world scenarios where operators settle in tranches.

**Screen 6 — Reconciliation**
Click **Auto-Reconcile**: the engine fetches each transaction from the XRPL
ledger, decodes the memo, and matches amount, sender, receiver, and cycle ID
against the off-ledger record — all without manual input. Mismatches are flagged
as exceptions with a specific reason code.

**Screen 7 — Audit Log**
Every action taken in the system — authorization, issuance, settlement,
reconciliation — is recorded with actor, timestamp, and detail. Click
**Export CSV** to download the full audit trail in a format ready for
regulatory review.

---

## The Blockchain Proof

### XRPL Testnet
After running any transaction from the Settlement or Issuance screens, copy the
transaction hash shown on screen and paste it into
**https://testnet.xrpl.org/transactions/\<hash\>**. The decoded memo will show
the `SettlementCycleID`, `PaymentInstructionID`, and `TransactionType` fields
written by PostalSettle — proving the off-ledger record and on-chain transaction
are the same event.

### Solana Devnet
Three transactions from a complete USD-ST lifecycle are permanently recorded on
Solana Devnet. Each carries a JSON memo with the full settlement context.

| Step | Transaction |
|------|-------------|
| Issuance    | [View on Solana Explorer](https://explorer.solana.com/tx/Wu1xKfofwNvBjcL5C66ZTXjXxcadW4amfXHGbv7zYH7TDJtuuxngUeBh8Jvar2doFB53JKxoKdfUsr7V6vM57Du?cluster=devnet) |
| Settlement  | [View on Solana Explorer](https://explorer.solana.com/tx/4k7VTSatS7iUyzh7TtwteGT61QarfsXxUPZYvJUc1GhCUYk59JFgrGMmf7NCKfn2TDBSHyUQwaZbnf4QAdmu3pSn?cluster=devnet) |
| Redemption  | [View on Solana Explorer](https://explorer.solana.com/tx/JHj4HSQZeGrq3dJakbcXmu6qZWF5k3cwcvCz9AE9RQ3MUJfJSqRFo2qUGMWBcmj2rMEjLr1hxTyHkGcNWLEDZnD?cluster=devnet) |

On each page, expand the **Memo** field in the transaction detail to see the
`SettlementCycleID`, `Amount_USD`, and `Type` written on-chain by PostalSettle.

---

## What Phase 2 Looks Like

- **CSV-driven clearing:** Upload a UPU parcel data export and the netting engine
  calculates obligations automatically — no manual entry.
- **Real operator pilot:** UAE Post and Kenya Post participate with live wallets
  on the actual corridor, replacing one real SWIFT message per cycle.
- **Mainnet USDC:** Swap the testnet issued currency for real USDC on XRPL and
  Solana mainnet with production treasury controls and multi-sig authorization.
