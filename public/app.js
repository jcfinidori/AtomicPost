const runBtn = document.getElementById("runBtn")
const stdoutEl = document.getElementById("stdout")
const stderrEl = document.getElementById("stderr")

function value(id) {
  return document.getElementById(id).value.trim()
}

function apiRunPath() {
  const currentPath = window.location.pathname.endsWith("/")
    ? window.location.pathname
    : `${window.location.pathname}/`
  return `${currentPath}api/run`
}

function toQueryString(payload) {
  const params = new URLSearchParams()
  Object.entries(payload).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, value)
    }
  })
  return params.toString()
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

  const payload = {
    networkUrl: value("networkUrl"),
    currencyCode: value("currencyCode"),
    issueAmount: value("issueAmount"),
    distributeAmount: value("distributeAmount"),
    redeemAmount: value("redeemAmount"),
    settlementCycleId: value("settlementCycleId"),
    issuanceAuthId: value("issuanceAuthId"),
    paymentInstructionId: value("paymentInstructionId"),
  }

  try {
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
  } catch (error) {
    stderrEl.textContent = error.message
  } finally {
    runBtn.disabled = false
    runBtn.textContent = "Run PoC"
  }
})
