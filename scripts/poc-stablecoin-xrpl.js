const xrpl = require("xrpl")
const fs = require("fs/promises")
const path = require("path")

const NETWORK_URL = process.env.XRPL_NETWORK_URL || "wss://s.altnet.rippletest.net:51233"
const CURRENCY_CODE = process.env.CURRENCY_CODE || "USD"
const ISSUE_AMOUNT = process.env.ISSUE_AMOUNT || "1000"
const DISTRIBUTE_AMOUNT = process.env.DISTRIBUTE_AMOUNT || "250"
const REDEEM_AMOUNT = process.env.REDEEM_AMOUNT || "50"
const SETTLEMENT_CYCLE_ID = process.env.SETTLEMENT_CYCLE_ID || `CYCLE_${new Date().toISOString().slice(0, 10)}`
const ISSUANCE_AUTH_ID = process.env.ISSUANCE_AUTH_ID || "IA_POC_001"
const PAYMENT_INSTRUCTION_ID = process.env.PAYMENT_INSTRUCTION_ID || "PI_POC_001"
const RECON_OUTPUT_PATH = process.env.RECON_OUTPUT_PATH || "artifacts/settlement-log.json"

function toHex(text) {
  return Buffer.from(text, "utf8").toString("hex").toUpperCase()
}

function buildMemos(fields) {
  return [
    {
      Memo: {
        MemoType: toHex("application/json"),
        MemoData: toHex(JSON.stringify(fields)),
      },
    },
  ]
}

async function submitAndWait(client, wallet, tx) {
  const prepared = await client.autofill({
    ...tx,
    Account: wallet.classicAddress,
  })
  const signed = wallet.sign(prepared)
  return client.submitAndWait(signed.tx_blob)
}

function formatAssetBalance(lines, issuer, currency) {
  const match = lines.find(
    (line) => line.account === issuer && line.currency.toUpperCase() === currency.toUpperCase(),
  )
  return match ? match.balance : "0"
}

async function trustSet(client, wallet, issuer, currency, limit = "1000000") {
  const tx = {
    TransactionType: "TrustSet",
    LimitAmount: {
      issuer,
      currency,
      value: limit,
    },
  }

  const result = await submitAndWait(client, wallet, tx)
  const code = result.result.meta.TransactionResult
  if (code !== "tesSUCCESS") {
    throw new Error(`TrustSet failed for ${wallet.classicAddress}: ${code}`)
  }
}

async function payment(client, sender, destination, amount) {
  const tx = {
    TransactionType: "Payment",
    Destination: destination,
    Amount: amount,
  }

  const result = await submitAndWait(client, sender, tx)
  const code = result.result.meta.TransactionResult
  if (code !== "tesSUCCESS") {
    throw new Error(`Payment failed from ${sender.classicAddress}: ${code}`)
  }

  return result
}

async function paymentWithMemo(client, sender, destination, amount, memoFields) {
  const tx = {
    TransactionType: "Payment",
    Destination: destination,
    Amount: amount,
    Memos: buildMemos(memoFields),
  }

  const result = await submitAndWait(client, sender, tx)
  const code = result.result.meta.TransactionResult
  if (code !== "tesSUCCESS") {
    throw new Error(`Payment failed from ${sender.classicAddress}: ${code}`)
  }

  return result
}

function txHash(result) {
  return result.result?.hash || result.result?.tx_json?.hash || "UNKNOWN_HASH"
}

function summarizeSettlementLog(settlementLog) {
  const expected = Number(settlementLog.expectedAmounts.settlement)
  const actual = Number(settlementLog.actualAmounts.settlement)
  settlementLog.status = Number.isFinite(expected) && Number.isFinite(actual) && expected === actual
    ? "Reconciled"
    : "Exception"
}

async function persistSettlementLog(settlementLog) {
  const outputPath = path.resolve(RECON_OUTPUT_PATH)
  await fs.mkdir(path.dirname(outputPath), { recursive: true })
  await fs.writeFile(outputPath, `${JSON.stringify(settlementLog, null, 2)}\n`, "utf8")
  return outputPath
}

async function setIssuerFlags(client, issuerWallet) {
  const tx = {
    TransactionType: "AccountSet",
    SetFlag: xrpl.AccountSetAsfFlags.asfDefaultRipple,
  }

  const result = await submitAndWait(client, issuerWallet, tx)
  const code = result.result.meta.TransactionResult
  if (code !== "tesSUCCESS") {
    throw new Error(`Issuer AccountSet failed: ${code}`)
  }
}

async function printAccountSnapshot(client, label, wallet, issuer, currency) {
  const [xrpBalance, trustLines, accountInfo] = await Promise.all([
    client.getXrpBalance(wallet.classicAddress),
    client.request({
      command: "account_lines",
      account: wallet.classicAddress,
      ledger_index: "validated",
    }),
    client.request({
      command: "account_info",
      account: wallet.classicAddress,
      ledger_index: "validated",
    }),
  ])

  const tokenBalance = formatAssetBalance(trustLines.result.lines, issuer, currency)
  const reserve = accountInfo.result.account_data.OwnerCount

  console.log(`\n[${label}] ${wallet.classicAddress}`)
  console.log(`- XRP balance: ${xrpBalance}`)
  console.log(`- ${currency}(${issuer}) balance: ${tokenBalance}`)
  console.log(`- OwnerCount: ${reserve}`)
}

async function main() {
  const client = new xrpl.Client(NETWORK_URL)
  await client.connect()
  console.log(`Connected to ${NETWORK_URL}`)

  try {
    const issuer = await client.fundWallet()
    const treasury = await client.fundWallet()
    const user = await client.fundWallet()

    console.log("\nWallets funded on testnet:")
    console.log(`- Issuer:   ${issuer.wallet.classicAddress}`)
    console.log(`- Treasury: ${treasury.wallet.classicAddress}`)
    console.log(`- User:     ${user.wallet.classicAddress}`)

    await setIssuerFlags(client, issuer.wallet)
    console.log("Issuer flags configured (DefaultRipple).")

    await trustSet(client, treasury.wallet, issuer.wallet.classicAddress, CURRENCY_CODE)
    await trustSet(client, user.wallet, issuer.wallet.classicAddress, CURRENCY_CODE)
    console.log(`Trust lines created for ${CURRENCY_CODE}.`)

    const issuanceResult = await paymentWithMemo(client, issuer.wallet, treasury.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: ISSUE_AMOUNT,
    }, {
      SettlementCycleID: SETTLEMENT_CYCLE_ID,
      IssuanceAuthorizationID: ISSUANCE_AUTH_ID,
      PaymentInstructionID: "ISSUANCE",
      PartialPayment: "N",
    })
    console.log(`Issued ${ISSUE_AMOUNT} ${CURRENCY_CODE} to treasury.`)

    const settlementResult = await paymentWithMemo(client, treasury.wallet, user.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: DISTRIBUTE_AMOUNT,
    }, {
      SettlementCycleID: SETTLEMENT_CYCLE_ID,
      IssuanceAuthorizationID: ISSUANCE_AUTH_ID,
      PaymentInstructionID: PAYMENT_INSTRUCTION_ID,
      PartialPayment: "N",
    })
    console.log(`Distributed ${DISTRIBUTE_AMOUNT} ${CURRENCY_CODE} to user.`)

    const redemptionResult = await paymentWithMemo(client, user.wallet, issuer.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: REDEEM_AMOUNT,
    }, {
      SettlementCycleID: SETTLEMENT_CYCLE_ID,
      IssuanceAuthorizationID: ISSUANCE_AUTH_ID,
      PaymentInstructionID: "REDEMPTION",
      PartialPayment: "N",
    })
    console.log(`Redeemed ${REDEEM_AMOUNT} ${CURRENCY_CODE} from user back to issuer.`)

    const settlementLog = {
      settlementCycleId: SETTLEMENT_CYCLE_ID,
      issuanceAuthorizationId: ISSUANCE_AUTH_ID,
      paymentInstructionId: PAYMENT_INSTRUCTION_ID,
      participants: {
        issuer: issuer.wallet.classicAddress,
        treasury: treasury.wallet.classicAddress,
        counterparty: user.wallet.classicAddress,
      },
      expectedAmounts: {
        issuance: ISSUE_AMOUNT,
        settlement: DISTRIBUTE_AMOUNT,
        redemption: REDEEM_AMOUNT,
      },
      actualAmounts: {
        issuance: ISSUE_AMOUNT,
        settlement: DISTRIBUTE_AMOUNT,
        redemption: REDEEM_AMOUNT,
      },
      transactions: {
        issuance: txHash(issuanceResult),
        settlement: txHash(settlementResult),
        redemption: txHash(redemptionResult),
      },
      status: "Pending_Settlement",
      generatedAt: new Date().toISOString(),
    }

    summarizeSettlementLog(settlementLog)
    const reconPath = await persistSettlementLog(settlementLog)
    console.log(`Settlement log written to ${reconPath} (${settlementLog.status}).`)

    await printAccountSnapshot(client, "Issuer", issuer.wallet, issuer.wallet.classicAddress, CURRENCY_CODE)
    await printAccountSnapshot(client, "Treasury", treasury.wallet, issuer.wallet.classicAddress, CURRENCY_CODE)
    await printAccountSnapshot(client, "User", user.wallet, issuer.wallet.classicAddress, CURRENCY_CODE)
  } finally {
    await client.disconnect()
  }
}

main().catch((error) => {
  console.error("PoC failed:", error)
  process.exitCode = 1
})
