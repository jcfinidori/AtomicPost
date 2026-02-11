const http = require("http")
const fs = require("fs")
const path = require("path")
const { spawn } = require("child_process")

const DEFAULT_PORT = Number(process.env.PORT || 3000)
const MAX_PORT_ATTEMPTS = 10
const PUBLIC_DIR = path.join(__dirname, "public")

function sendJson(res, statusCode, body) {
  res.writeHead(statusCode, { "Content-Type": "application/json" })
  res.end(JSON.stringify(body))
}

function serveFile(res, filePath, contentType) {
  fs.readFile(filePath, (error, data) => {
    if (error) {
      res.writeHead(500, { "Content-Type": "text/plain" })
      res.end("Internal server error")
      return
    }

    res.writeHead(200, { "Content-Type": contentType })
    res.end(data)
  })
}

function parseRequestBody(req) {
  return new Promise((resolve, reject) => {
    let body = ""
    req.on("data", (chunk) => {
      body += chunk
      if (body.length > 1_000_000) {
        reject(new Error("Request body too large"))
      }
    })

    req.on("end", () => {
      if (!body) {
        resolve({})
        return
      }

      try {
        resolve(JSON.parse(body))
      } catch {
        reject(new Error("Invalid JSON body"))
      }
    })

    req.on("error", reject)
  })
}

function runPoc(overrides) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", ["scripts/poc-stablecoin-xrpl.js"], {
      cwd: __dirname,
      env: {
        ...process.env,
        ...overrides,
      },
    })

    let stdout = ""
    let stderr = ""

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString()
    })

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString()
    })

    child.on("error", reject)
    child.on("close", (code) => {
      resolve({ code, stdout, stderr })
    })
  })
}


function buildOverrides(input) {
  return {
    XRPL_NETWORK_URL: input.networkUrl,
    CURRENCY_CODE: input.currencyCode,
    ISSUE_AMOUNT: input.issueAmount,
    DISTRIBUTE_AMOUNT: input.distributeAmount,
    REDEEM_AMOUNT: input.redeemAmount,
    SETTLEMENT_CYCLE_ID: input.settlementCycleId,
    ISSUANCE_AUTH_ID: input.issuanceAuthId,
    PAYMENT_INSTRUCTION_ID: input.paymentInstructionId,
  }
}

function filterOverrides(overrides) {
  return Object.fromEntries(
    Object.entries(overrides).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  )
}


function getPathname(reqUrl) {
  try {
    return new URL(reqUrl, "http://localhost").pathname
  } catch {
    return reqUrl || "/"
  }
}

function isRunEndpoint(pathname) {
  return pathname === "/api/run" || pathname === "/api/run/" || pathname.endsWith("/api/run") || pathname.endsWith("/api/run/")
}

const server = http.createServer(async (req, res) => {
  const pathname = getPathname(req.url)

  if (req.method === "GET" && pathname === "/") {
    serveFile(res, path.join(PUBLIC_DIR, "index.html"), "text/html; charset=utf-8")
    return
  }

  if (req.method === "GET" && pathname === "/app.js") {
    serveFile(res, path.join(PUBLIC_DIR, "app.js"), "text/javascript; charset=utf-8")
    return
  }

  if (req.method === "POST" && isRunEndpoint(pathname)) {
    try {
      const body = await parseRequestBody(req)
      const filteredOverrides = filterOverrides(buildOverrides(body))
      const result = await runPoc(filteredOverrides)
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 400, { error: error.message })
    }
    return
  }

  if (req.method === "GET" && isRunEndpoint(pathname)) {
    try {
      const requestUrl = new URL(req.url, `http://localhost:${DEFAULT_PORT}`)
      const queryInput = {
        networkUrl: requestUrl.searchParams.get("networkUrl"),
        currencyCode: requestUrl.searchParams.get("currencyCode"),
        issueAmount: requestUrl.searchParams.get("issueAmount"),
        distributeAmount: requestUrl.searchParams.get("distributeAmount"),
        redeemAmount: requestUrl.searchParams.get("redeemAmount"),
        settlementCycleId: requestUrl.searchParams.get("settlementCycleId"),
        issuanceAuthId: requestUrl.searchParams.get("issuanceAuthId"),
        paymentInstructionId: requestUrl.searchParams.get("paymentInstructionId"),
      }

      const filteredOverrides = filterOverrides(buildOverrides(queryInput))
      const result = await runPoc(filteredOverrides)
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 400, { error: error.message })
    }
    return
  }


  if (pathname.includes("/api/")) {
    sendJson(res, 404, { error: "API route not found" })
    return
  }

  res.writeHead(404, { "Content-Type": "text/plain" })
  res.end("Not found")
})

function startServer(preferredPort) {
  let port = preferredPort
  let attempts = 0

  const tryListen = () => {
    server.listen(port)
  }

  server.on("listening", () => {
    console.log(`UI server running at http://localhost:${port}`)
  })

  server.on("error", (error) => {
    if (error.code === "EADDRINUSE" && attempts < MAX_PORT_ATTEMPTS) {
      attempts += 1
      port += 1
      console.warn(`Port in use, retrying on http://localhost:${port} ...`)
      setTimeout(tryListen, 50)
      return
    }

    console.error("Failed to start UI server:", error.message)
    process.exitCode = 1
  })

  tryListen()
}

startServer(DEFAULT_PORT)
