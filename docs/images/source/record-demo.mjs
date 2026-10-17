// Records docs/images/demo.gif: a scripted walkthrough of the running app.
//
// 1. Run the API and the web app WITHOUT the incident worker (scripts/dev.sh backend, scripts/dev.sh frontend), so
//    nothing starts a paid incident-team run; the walkthrough costs a few classifier calls.
// 2. It needs two incidents from live runs in your database: one whose plan is safety-blocked and waiting for a
//    decision, and one approved with its actions carried out (see docs/getting-started.md, "Try the AI incident team").
// 3. npm install playwright-core@1.64.1 (it matches Playwright's cached Chromium 1248 and ffmpeg 1013), then:
//      BLOCKED_REQUEST=<request id> APPROVED_REQUEST=<request id> APPROVED_WORK_ORDER=<work order id> \
//        node record-demo.mjs out
//      ffmpeg -i out/*.webm -vf "fps=6,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=80:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" -loop 0 ../demo.gif
import { chromium } from "playwright-core"
import os from "node:os"
import fs from "node:fs"

const OUT = process.argv[2] ?? "out"
const BASE = "http://localhost:5173"
const env = (name) => process.env[name] ?? (() => { throw new Error(`set ${name}`) })()
const BLOCKED = env("BLOCKED_REQUEST") // plan ready, safety-blocked
const APPROVED = env("APPROVED_REQUEST") // revised plan approved, actions carried out
const WORK_ORDER = env("APPROVED_WORK_ORDER") // the approved incident's work order, assigned to tech
const W = 1280, H = 800
const executablePath = `${os.homedir()}/Library/Caches/ms-playwright/chromium-1248/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`

fs.mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ executablePath, headless: true })
const context = await browser.newContext({
  viewport: { width: W, height: H },
  deviceScaleFactor: 1,
  colorScheme: "light",
  recordVideo: { dir: OUT, size: { width: W, height: H } },
})

// A visible cursor and a caption bar, re-created on every page load.
await context.addInitScript(() => {
  const install = () => {
    if (document.getElementById("demo-cursor")) return
    const cursor = document.createElement("div")
    cursor.id = "demo-cursor"
    Object.assign(cursor.style, {
      position: "fixed", left: "0px", top: "0px", width: "22px", height: "22px", marginLeft: "-11px", marginTop: "-11px",
      borderRadius: "50%", background: "rgba(37,99,235,0.35)", border: "2px solid #2563eb", zIndex: 2147483647,
      pointerEvents: "none", transition: "transform 0.12s",
    })
    document.body.appendChild(cursor)
    const caption = document.createElement("div")
    caption.id = "demo-caption"
    Object.assign(caption.style, {
      position: "fixed", left: "50%", bottom: "18px", transform: "translateX(-50%)", maxWidth: "1100px",
      padding: "12px 22px", borderRadius: "12px", background: "rgba(15,23,42,0.92)", color: "#fff",
      font: "600 19px/1.35 Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif", zIndex: 2147483646,
      pointerEvents: "none", boxShadow: "0 6px 24px rgba(0,0,0,0.25)", display: "none", textAlign: "center",
    })
    document.body.appendChild(caption)
    const saved = sessionStorage.getItem("demo-caption")
    if (saved) { caption.textContent = saved; caption.style.display = "block" }
    const pos = sessionStorage.getItem("demo-cursor")
    if (pos) { const [x, y] = pos.split(","); cursor.style.left = `${x}px`; cursor.style.top = `${y}px` }
    window.addEventListener("mousemove", (e) => {
      cursor.style.left = `${e.clientX}px`; cursor.style.top = `${e.clientY}px`
      sessionStorage.setItem("demo-cursor", `${e.clientX},${e.clientY}`)
    })
    window.addEventListener("mousedown", () => (cursor.style.transform = "scale(0.7)"))
    window.addEventListener("mouseup", () => (cursor.style.transform = "scale(1)"))
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install)
  else install()
})

const page = await context.newPage()
const pause = (ms) => page.waitForTimeout(ms)
const caption = async (text) => {
  await page.evaluate((t) => {
    sessionStorage.setItem("demo-caption", t)
    const el = document.getElementById("demo-caption")
    if (el) { el.textContent = t; el.style.display = t ? "block" : "none" }
  }, text)
}
const moveTo = async (locator) => {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 })
  await pause(250)
}
const click = async (locator) => { await moveTo(locator); await locator.click(); }
const scrollTo = async (locator, block = "start") => {
  await locator.evaluate((el, b) => el.scrollIntoView({ behavior: "smooth", block: b }), block)
  await pause(900)
}
const signInAs = async (user) => {
  await page.getByText("Choose a demo account").waitFor()
  await click(page.getByRole("button", { name: new RegExp(`· ${user}\\b`) }))
  await page.getByRole("button", { name: /Sign out/ }).waitFor()
  await pause(600)
}
const signOut = async () => {
  await click(page.getByRole("button", { name: /Sign out/ }))
  await page.getByText("Choose a demo account").waitFor()
}

// 1. Sign in as a tenant
await page.goto(BASE)
await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
await page.goto(BASE)
await caption("Sign in: each demo role sees only what its permissions allow")
await pause(1800)
await signInAs("alice")

// 2. Report a problem
await click(page.getByRole("link", { name: "Report a problem" }).first())
await caption("A tenant reports a problem in their own words: text, photo, voice note or scanned document")
await pause(1200)
const box = page.getByLabel("What's the problem?")
await click(box)
await box.pressSequentially("The kitchen tap in my flat keeps dripping, even when it's turned off fully.", { delay: 28 })
await pause(600)
await caption("Personal details are redacted, Content Safety checks the text, and the classifier suggests a category and priority")
await click(page.getByRole("button", { name: "Send report" }))
const outcome = page.getByText(/^Thanks, /)
await outcome.waitFor({ timeout: 60000 })
await pause(1200)
const said = await outcome.textContent()
// The classifier isn't deterministic: caption whichever route code actually chose.
await caption(
  said.includes("work order")
    ? "Clear and low priority, so code creates the work order automatically"
    : "The AI wasn't sure enough, so code sends the request to a person instead of guessing",
)
await pause(3600)

// 3. Facility manager: a safety-blocked incident plan
await signOut()
await caption("The facility manager sees what needs a person")
await signInAs("fm")
await click(page.getByRole("link", { name: "Review queue" }))
await pause(2200)
await caption("A high-priority boiler fault went to the AI incident team, which proposed a plan")
await page.goto(`${BASE}/requests/${BLOCKED}`)
const panel = page.getByText("Incident team").first()
await panel.waitFor()
await scrollTo(panel)
await pause(1600)
await caption("Every claim cites a manual, a procedure or a record, found by read-only tools")
await scrollTo(page.getByRole("region", { name: "Evidence" }), "center")
await pause(2600)
const approve = page.getByRole("button", { name: /Approve plan/ })
await scrollTo(approve, "center")
await moveTo(approve)
await caption("The gas work permit has expired: code blocks approval, so nobody can approve unsafe work")
await pause(3400)

// 4. An approved plan: the application acted, and the tenant notice
await caption("After a revision the plan was approved, and the application (not the AI) assigned the technician")
await page.goto(`${BASE}/requests/${APPROVED}`)
await page.getByText("Incident team").first().waitFor()
await scrollTo(page.getByRole("region", { name: "Proposed actions" }), "center")
await pause(3200)
const notice = page.getByRole("region", { name: "Tenant notice" })
await scrollTo(notice, "center")
await caption("The manager approves a notice for the tenant; it is machine-translated if they wrote in another language")
const subject = page.getByLabel("Subject")
await click(subject)
await subject.pressSequentially("Heating repair is under way", { delay: 30 })
const message = page.getByLabel("Message")
await click(message)
await message.pressSequentially(
  "Dear resident, a safety check is complete and a qualified engineer is repairing the boiler today. Thank you for your patience.",
  { delay: 14 },
)
await pause(500)
await click(page.getByRole("button", { name: "Approve and send" }))
await pause(2400)

// 5. The decision trail
await caption("Every step is on the record: checks, model calls, tool calls and human decisions")
const trail = page.getByRole("button", { name: "Show the decision trail" })
await scrollTo(trail, "center")
await click(trail)
await pause(1500)
await page.mouse.wheel(0, 650)
await pause(2600)

// 6. Technician and tenant
await signOut()
await caption("The technician the plan named now has the work order, assigned to them")
await signInAs("tech")
await page.goto(`${BASE}/work-orders/${WORK_ORDER}`)
await page.getByText("Assigned to").waitFor()
await pause(3200)
await signOut()
await caption("And the tenant reads the approved notice on their request")
await signInAs("alice")
await page.goto(`${BASE}/requests/${APPROVED}`)
await page.getByText("Messages from facility management").waitFor()
await pause(3200)
await caption("FacilityOps AI · code controls the flow, AI agents advise, people approve anything with consequences")
await pause(3600)

const video = page.video()
await context.close()
await browser.close()
console.log(await video.path())
