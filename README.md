# PoC Stablecoin on XRPL ("XPRL")

This repository contains a minimal proof-of-concept flow for launching a fiat-backed stablecoin on the XRP Ledger testnet.

> You wrote "XPRL". The ledger/network is generally referred to as **XRPL** (XRP Ledger), so this guide and script use XRPL naming.

## What this PoC demonstrates

- Creating three wallets on XRPL Testnet:
  - **Issuer** (token origin account)
  - **Treasury/Distributor** (operational account that receives/forwards issued tokens)
  - **User** (end holder)
- Configuring issuer account flags suitable for a managed token setup.
- Establishing trust lines from treasury and user to the issuer.
- Issuing a USD-denominated IOU token (`USD`) to treasury.
- Transferring a portion of issued tokens from treasury to user.
- Optionally redeeming user balance back to issuer.

## Architecture (PoC)

- **Issuer account**
  - Mints/burns IOU balances.
  - Typically has restrictive flags (`DefaultRipple`, `RequireAuth` depending on policy).
- **Treasury account**
  - Holds and distributes minted supply.
  - Keeps issuer private key operationally isolated from user-facing flows.
- **User account**
  - Must explicitly trust the issuer and currency via trust lines.

## Prerequisites

- Node.js 18+
- XRPL testnet access (public endpoint is used by default)

Install dependencies:

```bash
npm install
```

## Run the PoC

```bash
npm run poc
```

Run the server/UI (same process):

```bash
npm run server
```

You can also use:

```bash
npm run ui
npm start
npm run dev
```

Then open `http://localhost:3000` in your browser.
If port `3000` is already in use, the UI server now automatically retries on the next ports (for example `3001`, `3002`, ... up to 10 attempts).

You can also upload a settlement CSV in the UI to auto-populate amounts:
- Expected headers: `participant`, `total_outgoing_usd`, `total_incoming_usd`, `net_pst_usd`
- Selecting **All participants (consolidated)** fills totals across rows
- Selecting a specific participant fills values for that row
- Mapping used by the form: `issueAmount = total_incoming_usd`, `distributeAmount = total_outgoing_usd`, `redeemAmount = abs(net_pst_usd)`

If you see `JSON.parse` errors in the browser, update to the latest `public/app.js` and `ui-server.js` in this repo. The UI now handles non-JSON error responses and shows the raw server body for easier debugging.
If Replit/proxy returns `405 Method Not Allowed` HTML on `POST /api/run`, the frontend now automatically retries with `GET /api/run?...` and expects JSON from the same backend.
If your platform serves the app behind a path prefix, the frontend now builds the API URL from the current page path so `/api/run` requests still route correctly.


Optional environment variables:

- `SETTLEMENT_CYCLE_ID` (default: date-based value like `CYCLE_2026-02-11`)
- `APPROVAL_ID` (canonical memo field; falls back to `ISSUANCE_AUTH_ID`; default: `APP_POC_001`)
- `ISSUANCE_AUTH_ID` (legacy alias consumed when `APPROVAL_ID` is not set)
- `PAYMENT_INSTRUCTION_ID` (default: `PI_POC_001`)
- `BATCH_ID` (default: `BATCH_001`)
- `BATCH_MODE_ENABLED` (`true`/`false`, default: `true`)
- `BATCH_DAYS` (default: `1`)
- `BATCH_START_DATE` (optional `YYYY-MM-DD`, defaults from cycle date)
- `BATCH_END_DATE` (optional `YYYY-MM-DD`, defaults to `start + days - 1`)
- `BATCH_REFERENCE_IDS` (optional comma-separated daily obligation references)
- `PARTIAL_SETTLEMENT_ENABLED` (`true`/`false`, default: `true`)
- `SETTLEMENT_APPROVED_AMOUNT` (optional cap used to simulate partial settlement decisions)
- `RETRY_COUNT` (default: `0`)
- `MAX_RETRY_ATTEMPTS` (default: `3`)
- `RETRY_INTERVAL_CYCLES` (default: `1`)
- `TRUSTLINE_GOVERNANCE_ENFORCED` (`true`/`false`, default: `true`)
- `RECON_OUTPUT_PATH` (default: `artifacts/settlement-log.json`)


Canonical memo schema (locked for this PoC):

- `CycleID`
- `BatchID`
- `ApprovalID`
- `PaymentInstructionID`
- `PartialFlag`

All issuance, settlement, and redemption payments now use this same schema in XRPL memo JSON payloads.

Deterministic reconciliation now evaluates the settlement transaction against explicit rules:
- tx hash exists
- amount matches expected settlement amount
- treasury/counterparty addresses match expected direction
- canonical memo schema fields match
- partial-flag consistency with settlement decision (`N` full, `Y` partial)
- XRPL transaction result is `tesSUCCESS`

If a rule fails, status is `Exception` with explicit reason codes in the artifact (for example `AMOUNT_MISMATCH`, `MEMO_MISSING_OR_INVALID`, `COUNTERPARTY_MISMATCH`, `PARTIAL_PAYMENT`).

Partial settlement + retry behavior (PoC-safe implementation):
- If requested settlement exceeds approved/liquid amount, settlement is executed partially (`PartialFlag=Y`) when partial mode is enabled
- Remaining obligation is recorded as pending with retry metadata (`retryCount`, `nextRetryCount`, `nextCycleId`)
- If retry limit is exceeded, reconciliation status becomes `Exception` with `RETRY_LIMIT_EXCEEDED`
- If checks pass and pending amount remains, status is `Partial_Settled_Pending_Retry`

Batch + multi-day aggregation behavior (PoC-safe implementation):
- `DISTRIBUTE_AMOUNT` is treated as the daily net obligation amount
- When batch mode is enabled, requested settlement aggregates to `daily amount × BATCH_DAYS`
- Batch window metadata (`BatchStartDate`, `BatchEndDate`, `BatchDays`, optional `ReferenceObligationIDs`) is included in settlement memo payload and artifact
- Reconciliation still anchors to canonical IDs while preserving batch metadata for audit traceability

Trustline governance checks (PoC-safe implementation):
- Preflight checks are run before issuance, settlement, and redemption payments
- Checks validate trustline existence, destination trustline headroom, and sender balance sufficiency
- On failure (when enforcement enabled), the PoC blocks payment and returns an explicit governance error
- Successful runs store trustline check evidence in the reconciliation artifact (`trustlineGovernance`)

Expected flow:

1. Connect to testnet.
2. Fund issuer/treasury/user wallets from faucet.
3. Set issuer flags.
4. Add trust lines from treasury and user to issuer for `USD`.
5. Issue `1000 USD` from issuer to treasury.
6. Transfer `250 USD` from treasury to user.
7. Redeem `50 USD` from user back to issuer.
8. Print resulting balances and reserve details.
9. Write a reconciliation artifact (`artifacts/settlement-log.json`) with transaction hashes and status.


## Run on Replit

- Import this repo into a **Node.js Repl**.
- Install dependencies once with `npm install`.
- Click **Run** (uses `.replit`) or run `npm run server`.

Notes:
- Replit runs `npm run server` by default via `.replit`.
- The server uses `process.env.PORT`, so it works with Replit webview routing.
- If `POST /api/run` is blocked by a proxy, the frontend falls back to `GET /api/run?...`.

## Important production notes

This is only a demonstration. For a production stablecoin, you should also implement:

- KYC/AML, sanctions, and risk controls.
- Auth model (`RequireAuth`) and selective trust line authorization strategy.
- Freeze/Clawback policy (where legally and technically appropriate).
- Key custody and HSM-backed signing.
- Monitoring, reconciliation, and incident response.
- Legal structure, attestations/proof-of-reserves, and jurisdictional compliance.

## Files

- `scripts/poc-stablecoin-xrpl.js`: executable PoC flow.
- `ui-server.js`: tiny Node HTTP server that exposes a browser UI and `/api/run` endpoint.
- `public/index.html`: form-based UI for setting PoC inputs and viewing outputs.
- `public/app.js`: frontend logic for calling the run endpoint and rendering stdout/stderr.
- `package.json`: npm scripts and dependency setup.

