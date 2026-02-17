const runBtn = document.getElementById("runBtn")
const stdoutEl = document.getElementById("stdout")
const stderrEl = document.getElementById("stderr")
const csvFileInput = document.getElementById("csvFile")
const participantSelect = document.getElementById("participantSelect")
const applyCsvBtn = document.getElementById("applyCsvBtn")
const csvStatusEl = document.getElementById("csvStatus")
const csvPreviewEl = document.getElementById("csvPreview")
const uiStatusEl = document.getElementById("uiStatus")
const runSummaryEl = document.getElementById("runSummary")
const runSummaryChipsEl = document.getElementById("runSummaryChips")
const runAllCsvCyclesEl = document.getElementById("runAllCsvCycles")

let parsedCsvData = null

function value(id) {
  return document.getElementById(id).value.trim()
}

function setFieldValue(id, nextValue) {
  document.getElementById(id).value = nextValue
}


function parseBoolLike(value) {
  const normalized = String(value || "").trim().toLowerCase()
  if (["true", "1", "yes", "y"].includes(normalized)) {
    return true
  }
  if (["false", "0", "no", "n"].includes(normalized)) {
    return false
  }
  throw new Error(`Expected true/false value (got: ${value})`)
}

function ensureNonNegativeInteger(name, raw) {
  if (raw === "") {
    return
  }
  const parsed = Number.parseInt(raw, 10)
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer`)
  }
}

function ensureNumeric(name, raw) {
  if (raw === "") {
    return
  }
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative number`)
  }
}

function ensureDate(name, raw) {
  if (!raw) {
    return
  }
  const iso = /^\d{4}-\d{2}-\d{2}$/
  if (!iso.test(raw)) {
    throw new Error(`${name} must use YYYY-MM-DD format`)
  }
}

function validatePayload(payload) {
  ensureNumeric("issueAmount", payload.issueAmount)
  ensureNumeric("distributeAmount", payload.distributeAmount)
  ensureNumeric("redeemAmount", payload.redeemAmount)
  ensureNumeric("settlementApprovedAmount", payload.settlementApprovedAmount)
  ensureNonNegativeInteger("retryCount", payload.retryCount)
  ensureNonNegativeInteger("maxRetryAttempts", payload.maxRetryAttempts)
  ensureNonNegativeInteger("batchDays", payload.batchDays)
  ensureDate("batchStartDate", payload.batchStartDate)
  ensureDate("batchEndDate", payload.batchEndDate)
  parseBoolLike(payload.batchModeEnabled)
  parseBoolLike(payload.partialSettlementEnabled)
  parseBoolLike(payload.trustlineGovernanceEnforced)
  ensureNonNegativeInteger("retryIntervalCycles", payload.retryIntervalCycles)
  ensureNumeric("operatorBShare", payload.operatorBShare)
  parseBoolLike(payload.operatorCEnabled)
  parseBoolLike(payload.requireAuthEnabled)
  parseBoolLike(payload.defaultRippleEnabled)
  parseBoolLike(payload.trustlineAuthReportEnabled)
}


function summaryChip(label, variant) {
  const chip = document.createElement("span")
  chip.className = `chip ${variant}`
  chip.textContent = label
  return chip
}

function renderRunSummary(stdout, stderr) {
  runSummaryChipsEl.innerHTML = ""

  if (stderr && stderr.trim()) {
    runSummaryEl.textContent = "Run finished with stderr output. Review details below."
    runSummaryChipsEl.appendChild(summaryChip("stderr present", "warn"))
    return
  }

  const authReportMatch = stdout.match(/Trustline authorization report written to\s+(.+?)\./)
  if (authReportMatch) {
    runSummaryChipsEl.appendChild(summaryChip("auth report written", "ok"))
  }

  const multiCycleMatch = stdout.match(/Multi-cycle simulation completed: (\d+) cycles, (\d+) failed/)
  if (multiCycleMatch) {
    runSummaryEl.textContent = `Multi-cycle run complete: ${multiCycleMatch[1]} cycles`
    const failed = Number(multiCycleMatch[2])
    runSummaryChipsEl.appendChild(summaryChip(`cycles: ${multiCycleMatch[1]}`, "ok"))
    runSummaryChipsEl.appendChild(summaryChip(`failed: ${failed}`, failed > 0 ? "err" : "ok"))
    return
  }

  const statusMatch = stdout.match(/Settlement log written to\s+(.+?)\s+\(([^)]+)\)\./)
  if (!statusMatch) {
    runSummaryEl.textContent = "Run completed. Could not parse settlement status from stdout."
    runSummaryChipsEl.appendChild(summaryChip("status unknown", "warn"))
    return
  }

  const [, reconPath, status] = statusMatch
  runSummaryEl.textContent = `Settlement artifact: ${reconPath}`

  const normalized = String(status).toLowerCase()
  const variant = normalized.includes("reconciled")
    ? "ok"
    : normalized.includes("partial")
      ? "warn"
      : "err"

  runSummaryChipsEl.appendChild(summaryChip(`status: ${status}`, variant))
}

function apiRunPath() {
  const currentPath = window.location.pathname.endsWith("/")
    ? window.location.pathname
    : `${window.location.pathname}/`
  return `${currentPath}api/run`
}

function normalizeHeader(text) {
  return text.trim().toLowerCase().replace(/[\s-]+/g, "_")
}

function parseCsvLine(line) {
  const values = []
  let current = ""
  let inQuotes = false

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i]

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"'
        i += 1
      } else {
        inQuotes = !inQuotes
      }
      continue
    }

    if (char === "," && !inQuotes) {
      values.push(current.trim())
      current = ""
      continue
    }

    current += char
  }

  values.push(current.trim())
  return values
}

function parseNumber(raw) {
  const cleaned = String(raw || "").replace(/,/g, "").trim()
  const parsed = Number(cleaned)
  if (Number.isNaN(parsed)) {
    throw new Error(`Invalid numeric value: ${raw}`)
  }
  return parsed
}

function parseSettlementCsv(text) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  if (lines.length < 2) {
    throw new Error("CSV must contain a header row and at least one data row")
  }

  const headers = parseCsvLine(lines[0]).map(normalizeHeader)
  const requiredHeaders = ["participant", "total_outgoing_usd", "total_incoming_usd", "net_pst_usd"]

  requiredHeaders.forEach((key) => {
    if (!headers.includes(key)) {
      throw new Error(`Missing required column: ${key}`)
    }
  })

  const rows = lines.slice(1).map((line) => {
    const values = parseCsvLine(line)
    const record = {}

    headers.forEach((header, index) => {
      record[header] = values[index] || ""
    })

    return {
      participant: record.participant,
      totalOutgoingUsd: parseNumber(record.total_outgoing_usd),
      totalIncomingUsd: parseNumber(record.total_incoming_usd),
      netPstUsd: parseNumber(record.net_pst_usd),
    }
  })

  if (!rows.length) {
    throw new Error("No valid rows found in CSV")
  }

  const consolidated = rows.reduce(
    (acc, row) => ({
      participant: "ALL",
      totalOutgoingUsd: acc.totalOutgoingUsd + row.totalOutgoingUsd,
      totalIncomingUsd: acc.totalIncomingUsd + row.totalIncomingUsd,
      netPstUsd: acc.netPstUsd + row.netPstUsd,
    }),
    { participant: "ALL", totalOutgoingUsd: 0, totalIncomingUsd: 0, netPstUsd: 0 },
  )

  return { rows, consolidated }
}

function formatAmount(amount) {
  return Number(amount).toFixed(2).replace(/\.00$/, "")
}

function applyRowToForm(row, sourceLabel) {
  setFieldValue("issueAmount", formatAmount(row.totalIncomingUsd))
  setFieldValue("distributeAmount", formatAmount(row.totalOutgoingUsd))
  setFieldValue("redeemAmount", formatAmount(Math.abs(row.netPstUsd)))

  csvStatusEl.textContent = `${sourceLabel} applied. You can still edit values before running.`
}

function renderCsvPreview(data) {
  const firstThree = data.rows.slice(0, 3)
  const previewRows = firstThree
    .map(
      (row) =>
        `${row.participant}: outgoing=${formatAmount(row.totalOutgoingUsd)}, incoming=${formatAmount(row.totalIncomingUsd)}, net=${formatAmount(row.netPstUsd)}`,
    )
    .join("\n")

  csvPreviewEl.textContent = [
    `Parsed rows: ${data.rows.length}`,
    `Consolidated outgoing=${formatAmount(data.consolidated.totalOutgoingUsd)}, incoming=${formatAmount(data.consolidated.totalIncomingUsd)}, net=${formatAmount(data.consolidated.netPstUsd)}`,
    previewRows ? `Sample:\n${previewRows}` : "",
  ]
    .filter(Boolean)
    .join("\n")
}

function rebuildParticipantSelect(data) {
  participantSelect.innerHTML = ""

  const allOption = document.createElement("option")
  allOption.value = "__all__"
  allOption.textContent = "All participants (consolidated)"
  participantSelect.appendChild(allOption)

  data.rows.forEach((row) => {
    const option = document.createElement("option")
    option.value = row.participant
    option.textContent = row.participant
    participantSelect.appendChild(option)
  })

  participantSelect.disabled = false
  applyCsvBtn.disabled = false
}

function onApplyCsv() {
  if (!parsedCsvData) {
    csvStatusEl.textContent = "Upload and parse a CSV file first."
    return
  }

  const selectedParticipant = participantSelect.value

  if (selectedParticipant === "__all__") {
    applyRowToForm(parsedCsvData.consolidated, "Consolidated totals")
    return
  }

  const row = parsedCsvData.rows.find((item) => item.participant === selectedParticipant)
  if (!row) {
    csvStatusEl.textContent = `Participant not found: ${selectedParticipant}`
    return
  }

  applyRowToForm(row, `Participant ${selectedParticipant}`)
}

csvFileInput.addEventListener("change", async () => {
  const [file] = csvFileInput.files || []

  if (!file) {
    parsedCsvData = null
    participantSelect.disabled = true
    applyCsvBtn.disabled = true
    csvStatusEl.textContent = ""
    csvPreviewEl.textContent = ""
    return
  }

  try {
    const text = await file.text()
    parsedCsvData = parseSettlementCsv(text)
    rebuildParticipantSelect(parsedCsvData)
    renderCsvPreview(parsedCsvData)
    applyRowToForm(parsedCsvData.consolidated, "Consolidated totals")
  } catch (error) {
    parsedCsvData = null
    participantSelect.disabled = true
    applyCsvBtn.disabled = true
    csvStatusEl.textContent = error.message
    csvPreviewEl.textContent = ""
  }
})

applyCsvBtn.addEventListener("click", onApplyCsv)

function toQueryString(payload) {
  const params = new URLSearchParams()
  Object.entries(payload).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, value)
    }
  })
  return params.toString()
}


function buildCsvCycles(baseCycleId) {
  if (!parsedCsvData) {
    return []
  }

  const base = baseCycleId || `CYCLE_${new Date().toISOString().slice(0, 10)}`
  return parsedCsvData.rows.map((row, index) => ({
    participant: row.participant,
    settlementCycleId: `${base}_R${index + 1}`,
    issueAmount: formatAmount(row.totalIncomingUsd),
    distributeAmount: formatAmount(row.totalOutgoingUsd),
    redeemAmount: formatAmount(Math.abs(row.netPstUsd)),
  }))
}

async function runViaGetFallback(payload) {
  const qs = toQueryString(payload)
  const response = await fetch(`${apiRunPath()}?${qs}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  })

  const rawBody = await response.text()
  let data
  try {
    data = rawBody ? JSON.parse(rawBody) : {}
  } catch {
    throw new Error(`Fallback GET returned non-JSON response (status ${response.status}): ${rawBody.slice(0, 300)}`)
  }

  if (!response.ok) {
    throw new Error(data.error || "Fallback GET failed")
  }

  return data
}

runBtn.addEventListener("click", async () => {
  runBtn.disabled = true
  runBtn.textContent = "Running..."
  stdoutEl.textContent = ""
  stderrEl.textContent = ""
  uiStatusEl.textContent = ""
  runSummaryEl.textContent = "Running..."
  runSummaryChipsEl.innerHTML = ""

  const payload = {
    networkUrl: value("networkUrl"),
    currencyCode: value("currencyCode"),
    issueAmount: value("issueAmount"),
    distributeAmount: value("distributeAmount"),
    redeemAmount: value("redeemAmount"),
    settlementCycleId: value("settlementCycleId"),
    approvalId: value("approvalId"),
    issuanceAuthId: value("issuanceAuthId"),
    paymentInstructionId: value("paymentInstructionId"),
    batchId: value("batchId"),
    settlementApprovedAmount: value("settlementApprovedAmount"),
    retryCount: value("retryCount"),
    maxRetryAttempts: value("maxRetryAttempts"),
    partialSettlementEnabled: value("partialSettlementEnabled"),
    retryIntervalCycles: value("retryIntervalCycles"),
    trustlineGovernanceEnforced: value("trustlineGovernanceEnforced"),
    batchModeEnabled: value("batchModeEnabled"),
    batchDays: value("batchDays"),
    batchStartDate: value("batchStartDate"),
    batchEndDate: value("batchEndDate"),
    batchReferenceIds: value("batchReferenceIds"),
    operatorBShare: value("operatorBShare"),
    operatorCEnabled: value("operatorCEnabled"),
    requireAuthEnabled: value("requireAuthEnabled"),
    defaultRippleEnabled: value("defaultRippleEnabled"),
    trustlineAuthReportEnabled: value("trustlineAuthReportEnabled"),
    trustlineAuthReportPath: value("trustlineAuthReportPath"),
    reconOutputPath: value("reconOutputPath"),
  }

  try {
    if (runAllCsvCyclesEl.checked) {
      const cycles = buildCsvCycles(payload.settlementCycleId)
      if (!cycles.length) {
        throw new Error("Enable multi-cycle only after uploading a CSV with rows")
      }
      payload.csvCycles = cycles
    }

    validatePayload(payload)
    const response = await fetch(apiRunPath(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    })

    const rawBody = await response.text()
    let data

    try {
      data = rawBody ? JSON.parse(rawBody) : {}
    } catch {
      if (response.status === 405) {
        if (payload.csvCycles) {
          throw new Error("Multi-cycle mode requires POST /api/run support; GET fallback is not available")
        }
        data = await runViaGetFallback(payload)
      } else {
        throw new Error(`Server returned non-JSON response (status ${response.status}): ${rawBody.slice(0, 300)}`)
      }
    }

    if (!response.ok) {
      throw new Error(data.error || "Failed to run PoC")
    }

    stdoutEl.textContent = data.stdout || "(no stdout)"
    stderrEl.textContent = data.stderr || "(no stderr)"
    uiStatusEl.textContent = "Run completed."
    renderRunSummary(data.stdout || "", data.stderr || "")
  } catch (error) {
    stderrEl.textContent = error.message
    runSummaryEl.textContent = "Run failed before completion."
    runSummaryChipsEl.innerHTML = ""
    runSummaryChipsEl.appendChild(summaryChip("run failed", "err"))
  } finally {
    runBtn.disabled = false
    runBtn.textContent = "Run PoC"
  }
})
