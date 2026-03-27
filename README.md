# 🏦 AtomicPost
### Cross-Border parcel Stablecoin settlement for Postal Operators

> Replacing 7-30 day correspondent banking settlement with stablecoin infrastructure
> that settles in seconds: live on XRPL Testnet and Solana Devnet.

![Python](https://img.shields.io/badge/Python-3.9-blue)
![Flask](https://img.shields.io/badge/Flask-2.0-green)
![XRPL](https://img.shields.io/badge/XRPL-Testnet-blue)
![Solana](https://img.shields.io/badge/Solana-Devnet-purple)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 🚨 The Problem

The global cross-border parcel business still runs on outdated financial rails. Settlements pass through multiple correspondent banks; triggering: fees, delays, trapped liquidity, currency risk.

| Pain Point        | Current Reality              |
|-------------------|------------------------------|
| Settlement time   | 7 to 30 days                 |
| Correspondent banks | 2 to 3 hops               |
| Transaction cost  | 5 to 7% of value             |
| Visibility        | None until funds arrive      |
| Working capital   | Trapped in receivables       |
| Reconciliation    | Manual, error-prone          |

Postal operators in developing markets bear the highest cost and have uneven access to correspondant banks.

---

## ✅ The Solution

AtomicPost is a stablecoin treasury infrastructure that replaces correspondent
banking for cross-border postal settlement. It sits at the settlement layer. Same clearing logic: net positions, issue coins, execute. Final, in seconds. We’re building atomic settlement rails. No correspondent banks. No trapped cash. No FX exposure. This paves the way for programmable value-sharing.

```
┌─────────────────────────────────────────────┐
│           TRADITIONAL SETTLEMENT            │
│                                             │
│  Operator A → Bank → Correspondent          │
│            → Bank → Operator B             │
│                                             │
│  Time: 7-30 days  │  Cost: 5-7%            │
│  Hops: 2-3 banks  │  Visibility: None      │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│           POSTAL SETTLE                     │
│                                             │
│  Treasury confirms USD backing              │
│       ↓                                     │
│  Mint USD-ST stablecoin                     │
│       ↓                                     │
│  Operator A → XRPL/Solana → Operator B     │
│       ↓                                     │
│  Burn tokens, release USD                   │
│                                             │
│  Time: 3-5 sec  │  Cost: <0.01%            │
│  Hops: 0 banks  │  Visibility: Full        │
└─────────────────────────────────────────────┘
```

---

## ⛓️ Live Blockchain Transactions

These are real transactions executed during development. Publicly verifiable now.

### Solana Devnet — Complete USD-ST Lifecycle

| Step | Action                                           | Explorer |
|------|--------------------------------------------------|----------|
| 1    | USD-ST Issuance: treasury mints stablecoin      | [View on Solana](https://explorer.solana.com/tx/Wu1xKfofwNvBjcL5C66ZTXjXxcadW4amfXHGbv7zYH7TDJtuuxngUeBh8Jvar2doFB53JKxoKdfUsr7V6vM57Du?cluster=devnet) |
| 2    | Cross-Border Settlement: operator payment       | [View on Solana](https://explorer.solana.com/tx/4k7VTSatS7iUyzh7TtwteGT61QarfsXxUPZYvJUc1GhCUYk59JFgrGMmf7NCKfn2TDBSHyUQwaZbnf4QAdmu3pSn?cluster=devnet) |
| 3    | Token Redemption: burn and release USD          | [View on Solana](https://explorer.solana.com/tx/JHj4HSQZeGrq3dJakbcXmu6qZWF5k3cwcvCz9AE9RQ3MUJfJSqRFo2qUGMWBcmj2rMEjLr1hxTyHkGcNWLEDZnD?cluster=devnet) |

All three transactions demonstrate the complete stablecoin lifecycle: mint
fiat-backed tokens on demand, execute cross-border settlement, burn tokens to
release USD backing.

### XRPL Testnet — full settlement dashboard

Complete 7-screen treasury application with real XRPL transactions including
issuance, settlement, partial settlement with carry-forward, and reconciliation.

---

## 🎯 Key Features

### Stablecoin treasury control
- Manual USD backing confirmation per cycle
- Single-action issuance authorization
- Real-time reserve utilization monitoring
- Hard cap enforcement on issuance

### Cross-Border settlement
- Full and partial settlement execution
- Automatic carry-forward for partial obligations
- Inbound and outbound settlement tracking
- Settlement comparison: traditional vs blockchain

### Blockchain integration
- XRPL Testnet: native issued currency
- Solana Devnet: SPL token program
- Chain-agnostic architecture
- Real transaction verification

### Treasury operations
- Automated reconciliation engine
- Exception detection and management
- Full audit trail with timestamps
- CSV export for regulatory review

---

## Hackathon track alignment

**Cross-Border stablecoin treasury**

| Requirement                               | AtomicPost                                      |
|-------------------------------------------|-------------------------------------------------|
| Mint fiat-backed stablecoins on demand    | ✅ USD-ST issuance against confirmed USD        |
| Redeem stablecoins                        | ✅ Burn flow with USD release                   |
| Move money across borders instantly       | ✅ 3-5 second settlement on XRPL/Solana         |
| Automate treasury operations              | ✅ 7-screen dashboard with full lifecycle       |
| Fewer intermediaries                      | ✅ Zero correspondent banks                     |
| Audit and compliance                      | ✅ Immutable on-chain audit trail               |

---

## Architecture

```
CSV Parcel Data
      ↓
Off-Ledger Clearing Engine
      ↓
Treasury Dashboard (Flask)
      ↓
┌─────────────┬──────────────┐
│ XRPL Layer  │ Solana Layer │
│ xrpl-py     │ solana-py    │
│ Issued curr │ SPL Token    │
└─────────────┴──────────────┘
      ↓
Reconciliation Engine
      ↓
Audit Log + CSV Export
```

### Application screens

| Screen              | EPIC   | Function                           |
|---------------------|--------|------------------------------------|
| Dashboard           | EPIC 0 | Overview and quick actions         |
| Clearing Setup      | EPIC 0 | Net obligation entry               |
| Reserve Governance  | EPIC 1 | USD backing and authorization      |
| Issuance            | EPIC 2 | Mint and burn USD-ST               |
| Settlement          | EPIC 3+4 | Execute XRPL payments            |
| Reconciliation      | EPIC 5 | Auto-match transactions            |
| Audit Log           | EPIC 6 | Trail and CSV export               |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9 or higher
- Git

### Installation

```bash
git clone https://github.com/jcfinidori/AtomicPost.git
cd AtomicPost
pip install -r requirements.txt
cp accounts_template.json accounts.json
```

### Configure Wallets

Edit `accounts.json` and add your XRPL testnet wallet credentials.
Get free testnet wallets at: https://faucet.altnet.rippletest.net/accounts

### Run the application

```bash
python3 reset_demo.py
python3 app.py
```

Open http://localhost:5000

### Run Solana demo

```bash
python3 solana_service.py
```

---

## 📁 Project structure

```
apostal_poc/
├── app.py                  # Flask application
├── database.py             # SQLite data layer
├── xrpl_service.py         # XRPL integration
├── solana_service.py       # Solana integration
├── reset_demo.py           # Demo reset script
├── requirements.txt        # Dependencies
├── accounts_template.json  # Wallet template
├── templates/              # Seven HTML screens
│   ├── base.html
│   ├── dashboard.html
│   ├── clearing.html
│   ├── reserve.html
│   ├── issuance.html
│   ├── settlement.html
│   ├── reconciliation.html
│   └── audit.html
└── sample_data/            # Sample CSV files
```

---

## 🗺️ Roadmap

**Phase 1 — Current PoC**
- Complete treasury dashboard on testnet
- Real blockchain transactions verified
- Solana devnet integration

**Phase 2 — CSV integration**
- Automated clearing from parcel CSV data
- Multi-operator netting engine

**Phase 3 — Operator Pilot**
- Real designated operator participation
- UAE Post and Kenya Post corridor

**Phase 4 — Mainnet**
- Real USDC on XRPL and Solana mainnet
- Production treasury controls

**Phase 5 — Multi-chain**
- Liquidity pooling across chains
- Automated FX conversion

---

## 🌍 Real-World context

I witness operators struggling with legacy banking infra, high inflation, currency instability. And now the global system itself is shifting; causing fragmentation.

Built for settlement corridors in complement of the Universal Postal Union (UPU) designated operator clearing rules.

**Target corridors:** Middle East to Africa; Asia/Pacific; Europe to Africa
**Operators:** Designated postal operators

This PoC demonstrates that postal operators can settle cross-border obligations
without correspondent banking infrastructure; making financial settlement
accessible to operators in developing markets.

---

## 📄 License

MIT License — see LICENSE file
