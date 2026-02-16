const xrpl = require("xrpl")
const fs = require("fs/promises")
const path = require("path")

const NETWORK_URL = process.env.XRPL_NETWORK_URL || "wss://s.altnet.rippletest.net:51233"
const CURRENCY_CODE = process.env.CURRENCY_CODE || "USD"
const ISSUE_AMOUNT = process.env.ISSUE_AMOUNT || "1000"
const DISTRIBUTE_AMOUNT = process.env.DISTRIBUTE_AMOUNT || "250"
const REDEEM_AMOUNT = process.env.REDEEM_AMOUNT || "50"
const SETTLEMENT_CYCLE_ID = process.env.SETTLEMENT_CYCLE_ID || `CYCLE_${new Date().toISOString().slice(0, 10)}`
const BATCH_ID = process.env.BATCH_ID || "BATCH_001"
const APPROVAL_ID = process.env.APPROVAL_ID || process.env.ISSUANCE_AUTH_ID || "APP_POC_001"
const PAYMENT_INSTRUCTION_ID = process.env.PAYMENT_INSTRUCTION_ID || "PI_POC_001"
const PARTIAL_SETTLEMENT_ENABLED = String(process.env.PARTIAL_SETTLEMENT_ENABLED || "true").toLowerCase() !== "false"
const SETTLEMENT_APPROVED_AMOUNT = process.env.SETTLEMENT_APPROVED_AMOUNT
const RETRY_COUNT = process.env.RETRY_COUNT || "0"
const MAX_RETRY_ATTEMPTS = process.env.MAX_RETRY_ATTEMPTS || "3"
const RETRY_INTERVAL_CYCLES = process.env.RETRY_INTERVAL_CYCLES || "1"
const BATCH_MODE_ENABLED = String(process.env.BATCH_MODE_ENABLED || "true").toLowerCase() !== "false"
const BATCH_DAYS = process.env.BATCH_DAYS || "1"
const BATCH_START_DATE = process.env.BATCH_START_DATE
const BATCH_END_DATE = process.env.BATCH_END_DATE
const BATCH_REFERENCE_IDS = process.env.BATCH_REFERENCE_IDS || ""
const TRUSTLINE_GOVERNANCE_ENFORCED = String(process.env.TRUSTLINE_GOVERNANCE_ENFORCED || "true").toLowerCase() !== "false"
const RECON_OUTPUT_PATH = process.env.RECON_OUTPUT_PATH || "artifacts/settlement-log.json"

const MEMO_SCHEMA_KEYS = Object.freeze([
  "CycleID",
  "BatchID",
  "ApprovalID",
  "PaymentInstructionID",
  "PartialFlag",
])

const EXCEPTION_CODES = Object.freeze({
  MISSING_TX_HASH: "MISSING_TX_HASH",
  AMOUNT_MISMATCH: "AMOUNT_MISMATCH",
  COUNTERPARTY_MISMATCH: "COUNTERPARTY_MISMATCH",
  MEMO_MISSING_OR_INVALID: "MEMO_MISSING_OR_INVALID",
  PARTIAL_PAYMENT: "PARTIAL_PAYMENT",
  STATUS_NOT_SUCCESS: "STATUS_NOT_SUCCESS",
  RETRY_LIMIT_EXCEEDED: "RETRY_LIMIT_EXCEEDED",
  TRUSTLINE_MISSING: "TRUSTLINE_MISSING",
  TRUSTLINE_LIMIT_EXCEEDED: "TRUSTLINE_LIMIT_EXCEEDED",
  INSUFFICIENT_SENDER_BALANCE: "INSUFFICIENT_SENDER_BALANCE",
  TRUSTLINE_PRECHECK_FAILED: "TRUSTLINE_PRECHECK_FAILED",
})

function toHex(text) {
  return Buffer.from(text, "utf8").toString("hex").toUpperCase()
}

function asPositiveNumber(name, value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative number, got: ${value}`)
  }
  return parsed
}

function asNonNegativeInt(name, value) {
  const parsed = Number.parseInt(String(value), 10)
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer, got: ${value}`)
  }
  return parsed
}

function isFilledString(value) {
  return typeof value === "string" && value.trim() !== ""
}

function assertCanonicalId(name, value) {
  if (!isFilledString(value)) {
    throw new Error(`${name} is required for canonical memo schema`)
  }
}

function assertPartialFlag(value) {
  if (value !== "Y" && value !== "N") {
    throw new Error(`PartialFlag must be 'Y' or 'N', got: ${value}`)
  }
}

function createCanonicalMemoFields(paymentInstructionId, partialFlag) {
  assertCanonicalId("CycleID", SETTLEMENT_CYCLE_ID)
  assertCanonicalId("BatchID", BATCH_ID)
  assertCanonicalId("ApprovalID", APPROVAL_ID)
  assertCanonicalId("PaymentInstructionID", paymentInstructionId)
  assertPartialFlag(partialFlag)

  return {
    CycleID: SETTLEMENT_CYCLE_ID,
    BatchID: BATCH_ID,
    ApprovalID: APPROVAL_ID,
    PaymentInstructionID: paymentInstructionId,
    PartialFlag: partialFlag,
  }
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

function readIssuedCurrencyValue(result) {
  const amount = result?.result?.tx_json?.Amount
  if (typeof amount === "object" && amount?.value !== undefined) {
    return String(amount.value)
  }
  return "0"
}

function txHash(result) {
  return result.result?.hash || result.result?.tx_json?.hash || "UNKNOWN_HASH"
}

function txResultCode(result) {
  return result.result?.meta?.TransactionResult || "UNKNOWN_RESULT"
}

function txAccount(result) {
  return result.result?.tx_json?.Account || ""
}

function txDestination(result) {
  return result.result?.tx_json?.Destination || ""
}

function computeNextCycleId(currentCycleId, step = 1) {
  const match = /^CYCLE_(\d{4}-\d{2}-\d{2})$/.exec(currentCycleId)
  if (!match) {
    return `${currentCycleId}_RETRY_${step}`
  }

  const currentDate = new Date(`${match[1]}T00:00:00Z`)
  if (Number.isNaN(currentDate.getTime())) {
    return `${currentCycleId}_RETRY_${step}`
  }

  currentDate.setUTCDate(currentDate.getUTCDate() + step)
  return `CYCLE_${currentDate.toISOString().slice(0, 10)}`
}

function inferCycleDate(cycleId) {
  const match = /^CYCLE_(\d{4}-\d{2}-\d{2})$/.exec(cycleId)
  if (!match) {
    return new Date().toISOString().slice(0, 10)
  }
  return match[1]
}

function parseDateOrThrow(name, value) {
  const parsed = new Date(`${value}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`${name} must be YYYY-MM-DD, got: ${value}`)
  }
  return parsed
}

function formatDate(date) {
  return date.toISOString().slice(0, 10)
}

function parseReferenceIds(raw) {
  if (!raw) {
    return []
  }
  return String(raw)
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
}

function parseAmount(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

async function findTrustline(client, account, issuer, currency) {
  const response = await client.request({
    command: "account_lines",
    account,
    peer: issuer,
    ledger_index: "validated",
  })

  return response.result.lines.find(
    (line) => line.account === issuer && line.currency.toUpperCase() === currency.toUpperCase(),
  ) || null
}

function trustlineCheck(name, pass, reasonCode, details) {
  return {
    name,
    pass,
    ...(pass ? {} : { reasonCode }),
    details,
  }
}

async function evaluateTrustlineGovernance(client, params) {
  const {
    senderAddress,
    destinationAddress,
    issuerAddress,
    currency,
    amount,
    stage,
  } = params

  const amountNumber = parseAmount(amount)
  const checks = []

  let senderLine = null
  if (senderAddress !== issuerAddress) {
    senderLine = await findTrustline(client, senderAddress, issuerAddress, currency)
    checks.push(
      trustlineCheck(
        "sender_trustline_exists",
        Boolean(senderLine),
        EXCEPTION_CODES.TRUSTLINE_MISSING,
        {
          stage,
          senderAddress,
          issuerAddress,
        },
      ),
    )

    if (senderLine) {
      const senderBalance = parseAmount(senderLine.balance)
      checks.push(
        trustlineCheck(
          "sender_balance_sufficient",
          senderBalance >= amountNumber,
          EXCEPTION_CODES.INSUFFICIENT_SENDER_BALANCE,
          {
            stage,
            senderBalance,
            amount: amountNumber,
          },
        ),
      )
    }
  }

  let destinationLine = null
  if (destinationAddress !== issuerAddress) {
    destinationLine = await findTrustline(client, destinationAddress, issuerAddress, currency)
    checks.push(
      trustlineCheck(
        "destination_trustline_exists",
        Boolean(destinationLine),
        EXCEPTION_CODES.TRUSTLINE_MISSING,
        {
          stage,
          destinationAddress,
          issuerAddress,
        },
      ),
    )

    if (destinationLine) {
      const destinationBalance = parseAmount(destinationLine.balance)
      const destinationLimit = parseAmount(destinationLine.limit)
      const projectedBalance = destinationBalance + amountNumber

      checks.push(
        trustlineCheck(
          "destination_limit_not_exceeded",
          projectedBalance <= destinationLimit,
          EXCEPTION_CODES.TRUSTLINE_LIMIT_EXCEEDED,
          {
            stage,
            destinationBalance,
            destinationLimit,
            projectedBalance,
            amount: amountNumber,
          },
        ),
      )
    }
  }

  const failures = checks.filter((check) => !check.pass)
  return {
    stage,
    enforced: TRUSTLINE_GOVERNANCE_ENFORCED,
    pass: failures.length === 0,
    checks,
    exceptionReasons: [...new Set(failures.map((f) => f.reasonCode))],
  }
}

function enforceTrustlineGovernanceResult(result) {
  if (!TRUSTLINE_GOVERNANCE_ENFORCED || result.pass) {
    return
  }

  throw new Error(
    `Trustline governance precheck failed at ${result.stage}: ${result.exceptionReasons.join(", ")}`,
  )
}

function buildSettlementPlan() {
  const dailySettlementAmount = asPositiveNumber("DISTRIBUTE_AMOUNT", DISTRIBUTE_AMOUNT)
  const issuanceAmount = asPositiveNumber("ISSUE_AMOUNT", ISSUE_AMOUNT)
  const retryCount = asNonNegativeInt("RETRY_COUNT", RETRY_COUNT)
  const maxRetryAttempts = asNonNegativeInt("MAX_RETRY_ATTEMPTS", MAX_RETRY_ATTEMPTS)
  const retryIntervalCycles = asNonNegativeInt("RETRY_INTERVAL_CYCLES", RETRY_INTERVAL_CYCLES)

  const batchDays = BATCH_MODE_ENABLED ? Math.max(asNonNegativeInt("BATCH_DAYS", BATCH_DAYS), 1) : 1
  const requestedSettlement = dailySettlementAmount * batchDays

  const inferredStart = inferCycleDate(SETTLEMENT_CYCLE_ID)
  const batchStartDate = BATCH_START_DATE && BATCH_START_DATE.trim() !== ""
    ? BATCH_START_DATE.trim()
    : inferredStart
  const batchStart = parseDateOrThrow("BATCH_START_DATE", batchStartDate)

  const computedEnd = new Date(batchStart)
  computedEnd.setUTCDate(computedEnd.getUTCDate() + batchDays - 1)

  const batchEndDate = BATCH_END_DATE && BATCH_END_DATE.trim() !== ""
    ? BATCH_END_DATE.trim()
    : formatDate(computedEnd)
  parseDateOrThrow("BATCH_END_DATE", batchEndDate)

  const batchReferenceIds = parseReferenceIds(BATCH_REFERENCE_IDS)

  const approvedCap = SETTLEMENT_APPROVED_AMOUNT === undefined || SETTLEMENT_APPROVED_AMOUNT === ""
    ? requestedSettlement
    : asPositiveNumber("SETTLEMENT_APPROVED_AMOUNT", SETTLEMENT_APPROVED_AMOUNT)

  const availableForSettlement = Math.min(issuanceAmount, approvedCap)
  let instructedSettlement = requestedSettlement

  if (requestedSettlement > availableForSettlement) {
    if (!PARTIAL_SETTLEMENT_ENABLED) {
      throw new Error(
        `Requested settlement (${requestedSettlement}) exceeds available amount (${availableForSettlement}) while PARTIAL_SETTLEMENT_ENABLED=false`,
      )
    }
    instructedSettlement = availableForSettlement
  }

  const pendingAmount = Math.max(requestedSettlement - instructedSettlement, 0)
  const partialFlag = pendingAmount > 0 ? "Y" : "N"
  const nextRetryCount = pendingAmount > 0 ? retryCount + 1 : retryCount
  const hasRetriesRemaining = nextRetryCount < maxRetryAttempts

  return {
    batch: {
      batchModeEnabled: BATCH_MODE_ENABLED,
      batchId: BATCH_ID,
      batchDays,
      batchStartDate,
      batchEndDate,
      batchReferenceIds,
      dailySettlementAmount,
    },
    requestedSettlement,
    issuanceAmount,
    approvedCap,
    instructedSettlement,
    pendingAmount,
    partialFlag,
    retry: {
      retryCount,
      nextRetryCount,
      maxRetryAttempts,
      retryIntervalCycles,
      hasRetriesRemaining,
      nextCycleId: pendingAmount > 0 && hasRetriesRemaining
        ? computeNextCycleId(SETTLEMENT_CYCLE_ID, Math.max(retryIntervalCycles, 1))
        : null,
      escalationRequired: pendingAmount > 0 && !hasRetriesRemaining,
    },
  }
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
  const code = txResultCode(result)
  if (code !== "tesSUCCESS") {
    throw new Error(`TrustSet failed for ${wallet.classicAddress}: ${code}`)
  }
}

async function paymentWithMemo(client, sender, destination, amount, memoFields) {
  const tx = {
    TransactionType: "Payment",
    Destination: destination,
    Amount: amount,
    Memos: buildMemos(memoFields),
  }

  const result = await submitAndWait(client, sender, tx)
  const code = txResultCode(result)
  if (code !== "tesSUCCESS") {
    throw new Error(`Payment failed from ${sender.classicAddress}: ${code}`)
  }

  return result
}

function memoSchemaMatches(expectedMemo, observedMemo) {
  const mismatches = []

  MEMO_SCHEMA_KEYS.forEach((key) => {
    if (String(expectedMemo[key]) !== String(observedMemo[key])) {
      mismatches.push(key)
    }
  })

  return {
    pass: mismatches.length === 0,
    mismatches,
  }
}

function buildCheck(name, pass, reasonCode, expected, observed, details) {
  return {
    name,
    pass,
    ...(pass ? {} : { reasonCode }),
    expected,
    observed,
    ...(details ? { details } : {}),
  }
}

function summarizeSettlementLog(settlementLog) {
  const checks = []
  const settlementTxHash = settlementLog.transactions.settlement
  const settlementExpected = Number(settlementLog.expectedAmounts.settlement)
  const settlementActual = Number(settlementLog.actualAmounts.settlement)
  const settlementDetail = settlementLog.transactionDetails.settlement

  checks.push(
    buildCheck(
      "tx_hash_present",
      settlementTxHash !== "UNKNOWN_HASH",
      EXCEPTION_CODES.MISSING_TX_HASH,
      "non-empty hash",
      settlementTxHash,
    ),
  )

  checks.push(
    buildCheck(
      "amount_matches",
      Number.isFinite(settlementExpected) && Number.isFinite(settlementActual) && settlementExpected === settlementActual,
      EXCEPTION_CODES.AMOUNT_MISMATCH,
      settlementExpected,
      settlementActual,
    ),
  )

  checks.push(
    buildCheck(
      "counterparties_match",
      settlementDetail.account === settlementLog.participants.treasury
        && settlementDetail.destination === settlementLog.participants.counterparty,
      EXCEPTION_CODES.COUNTERPARTY_MISMATCH,
      {
        account: settlementLog.participants.treasury,
        destination: settlementLog.participants.counterparty,
      },
      {
        account: settlementDetail.account,
        destination: settlementDetail.destination,
      },
    ),
  )

  const memoComparison = memoSchemaMatches(
    createCanonicalMemoFields(PAYMENT_INSTRUCTION_ID, settlementLog.settlementPlan.partialFlag),
    settlementLog.memos.settlement,
  )
  checks.push(
    buildCheck(
      "memo_schema_match",
      memoComparison.pass,
      EXCEPTION_CODES.MEMO_MISSING_OR_INVALID,
      MEMO_SCHEMA_KEYS,
      MEMO_SCHEMA_KEYS.filter((key) => key in settlementLog.memos.settlement),
      memoComparison.pass ? undefined : { mismatches: memoComparison.mismatches },
    ),
  )

  checks.push(
    buildCheck(
      "partial_flag_consistency",
      settlementLog.memos.settlement.PartialFlag === settlementLog.settlementPlan.partialFlag,
      EXCEPTION_CODES.PARTIAL_PAYMENT,
      settlementLog.settlementPlan.partialFlag,
      settlementLog.memos.settlement.PartialFlag,
    ),
  )

  checks.push(
    buildCheck(
      "ledger_result_success",
      settlementDetail.resultCode === "tesSUCCESS",
      EXCEPTION_CODES.STATUS_NOT_SUCCESS,
      "tesSUCCESS",
      settlementDetail.resultCode,
    ),
  )

  checks.push(
    buildCheck(
      "retry_limit_not_exceeded",
      !settlementLog.retry.escalationRequired,
      EXCEPTION_CODES.RETRY_LIMIT_EXCEEDED,
      `retry < ${settlementLog.retry.maxRetryAttempts}`,
      `retry = ${settlementLog.retry.nextRetryCount}`,
    ),
  )

  checks.push(
    buildCheck(
      "trustline_governance_precheck",
      settlementLog.trustlineGovernance.settlement.pass,
      EXCEPTION_CODES.TRUSTLINE_PRECHECK_FAILED,
      true,
      settlementLog.trustlineGovernance.settlement.pass,
      {
        reasons: settlementLog.trustlineGovernance.settlement.exceptionReasons,
      },
    ),
  )

  const failures = checks.filter((check) => !check.pass)
  settlementLog.reconciliation = {
    method: "deterministic_rule_matcher_v2",
    checkedAt: new Date().toISOString(),
    checks,
    exceptionReasons: [...new Set(failures.map((failure) => failure.reasonCode))],
    resolution: failures.length
      ? {
        status: "Open",
        action: settlementLog.retry.escalationRequired ? "Escalate" : "Manual_Review_Required",
        notes: settlementLog.retry.escalationRequired
          ? "Retry limit exceeded. Escalate to treasury manual intervention."
          : "Investigate exception reasons and resolve with retry or manual action.",
      }
      : {
        status: settlementLog.settlementPlan.pendingAmount > 0 ? "Retry_Scheduled" : "Not_Required",
      },
  }

  if (failures.length > 0) {
    settlementLog.status = "Exception"
    return
  }

  settlementLog.status = settlementLog.settlementPlan.pendingAmount > 0
    ? "Partial_Settled_Pending_Retry"
    : "Reconciled"
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
  const code = txResultCode(result)
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
    const settlementPlan = buildSettlementPlan()

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

    const issuanceTrustlineCheck = await evaluateTrustlineGovernance(client, {
      stage: "issuance",
      senderAddress: issuer.wallet.classicAddress,
      destinationAddress: treasury.wallet.classicAddress,
      issuerAddress: issuer.wallet.classicAddress,
      currency: CURRENCY_CODE,
      amount: ISSUE_AMOUNT,
    })
    enforceTrustlineGovernanceResult(issuanceTrustlineCheck)

    const issuanceMemo = createCanonicalMemoFields("ISSUANCE", "N")
    const issuanceResult = await paymentWithMemo(client, issuer.wallet, treasury.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: ISSUE_AMOUNT,
    }, issuanceMemo)
    console.log(`Issued ${ISSUE_AMOUNT} ${CURRENCY_CODE} to treasury.`)

    const settlementTrustlineCheck = await evaluateTrustlineGovernance(client, {
      stage: "settlement",
      senderAddress: treasury.wallet.classicAddress,
      destinationAddress: user.wallet.classicAddress,
      issuerAddress: issuer.wallet.classicAddress,
      currency: CURRENCY_CODE,
      amount: String(settlementPlan.instructedSettlement),
    })
    enforceTrustlineGovernanceResult(settlementTrustlineCheck)

    const settlementMemo = {
      ...createCanonicalMemoFields(PAYMENT_INSTRUCTION_ID, settlementPlan.partialFlag),
      BatchStartDate: settlementPlan.batch.batchStartDate,
      BatchEndDate: settlementPlan.batch.batchEndDate,
      BatchDays: String(settlementPlan.batch.batchDays),
      ReferenceObligationIDs: settlementPlan.batch.batchReferenceIds,
    }
    const settlementResult = await paymentWithMemo(client, treasury.wallet, user.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: String(settlementPlan.instructedSettlement),
    }, settlementMemo)
    console.log(`Distributed ${settlementPlan.instructedSettlement} ${CURRENCY_CODE} to user.`)

    const redemptionTrustlineCheck = await evaluateTrustlineGovernance(client, {
      stage: "redemption",
      senderAddress: user.wallet.classicAddress,
      destinationAddress: issuer.wallet.classicAddress,
      issuerAddress: issuer.wallet.classicAddress,
      currency: CURRENCY_CODE,
      amount: REDEEM_AMOUNT,
    })
    enforceTrustlineGovernanceResult(redemptionTrustlineCheck)

    const redemptionMemo = createCanonicalMemoFields("REDEMPTION", "N")
    const redemptionResult = await paymentWithMemo(client, user.wallet, issuer.wallet.classicAddress, {
      currency: CURRENCY_CODE,
      issuer: issuer.wallet.classicAddress,
      value: REDEEM_AMOUNT,
    }, redemptionMemo)
    console.log(`Redeemed ${REDEEM_AMOUNT} ${CURRENCY_CODE} from user back to issuer.`)

    const settlementLog = {
      canonicalIds: {
        CycleID: SETTLEMENT_CYCLE_ID,
        BatchID: BATCH_ID,
        ApprovalID: APPROVAL_ID,
        PaymentInstructionID: PAYMENT_INSTRUCTION_ID,
        PartialFlag: settlementPlan.partialFlag,
      },
      memoSchemaKeys: MEMO_SCHEMA_KEYS,
      settlementCycleId: SETTLEMENT_CYCLE_ID,
      issuanceAuthorizationId: APPROVAL_ID,
      paymentInstructionId: PAYMENT_INSTRUCTION_ID,
      participants: {
        issuer: issuer.wallet.classicAddress,
        treasury: treasury.wallet.classicAddress,
        counterparty: user.wallet.classicAddress,
      },
      trustlineGovernance: {
        issuance: issuanceTrustlineCheck,
        settlement: settlementTrustlineCheck,
        redemption: redemptionTrustlineCheck,
      },
      settlementPlan,
      batch: settlementPlan.batch,
      retry: settlementPlan.retry,
      expectedAmounts: {
        issuance: ISSUE_AMOUNT,
        settlementDaily: String(settlementPlan.batch.dailySettlementAmount),
        settlement: String(settlementPlan.instructedSettlement),
        settlementRequested: String(settlementPlan.requestedSettlement),
        settlementPending: String(settlementPlan.pendingAmount),
        redemption: REDEEM_AMOUNT,
      },
      actualAmounts: {
        issuance: readIssuedCurrencyValue(issuanceResult),
        settlement: readIssuedCurrencyValue(settlementResult),
        redemption: readIssuedCurrencyValue(redemptionResult),
      },
      transactions: {
        issuance: txHash(issuanceResult),
        settlement: txHash(settlementResult),
        redemption: txHash(redemptionResult),
      },
      transactionDetails: {
        issuance: {
          account: txAccount(issuanceResult),
          destination: txDestination(issuanceResult),
          resultCode: txResultCode(issuanceResult),
        },
        settlement: {
          account: txAccount(settlementResult),
          destination: txDestination(settlementResult),
          resultCode: txResultCode(settlementResult),
        },
        redemption: {
          account: txAccount(redemptionResult),
          destination: txDestination(redemptionResult),
          resultCode: txResultCode(redemptionResult),
        },
      },
      memos: {
        issuance: issuanceMemo,
        settlement: settlementMemo,
        redemption: redemptionMemo,
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
