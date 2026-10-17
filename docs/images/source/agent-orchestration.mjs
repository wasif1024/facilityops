// Source of docs/images/agent-orchestration-{light,dark}.png. Run `node agent-orchestration.mjs` to write the
// two SVGs, then open each in a browser (or Playwright) and export at 2x to PNG. Edit the layout here, not the PNGs.

import fs from "node:fs"

const THEMES = {
  light: {
    bg: "#ffffff", panel: "#f8fafc", panelStroke: "#cbd5e1", text: "#0f172a", muted: "#475569", faint: "#64748b",
    code: ["#f1f5f9", "#64748b"], ai: ["#f3e8ff", "#7c3aed"], person: ["#fef3c7", "#b45309"],
    danger: ["#fee2e2", "#dc2626"], arrow: "#475569", aiText: "#5b21b6", personText: "#92400e", dangerText: "#991b1b",
    band: ["#ecfdf5", "#059669"], bandText: "#065f46",
  },
  dark: {
    bg: "#0d1117", panel: "#161b22", panelStroke: "#30363d", text: "#e6edf3", muted: "#9da7b3", faint: "#8b949e",
    code: ["#1f2630", "#8b949e"], ai: ["#2a1f4d", "#a78bfa"], person: ["#3a2a0a", "#f59e0b"],
    danger: ["#3b1219", "#f87171"], arrow: "#9da7b3", aiText: "#c4b5fd", personText: "#fcd34d", dangerText: "#fca5a5",
    band: ["#0f2a22", "#34d399"], bandText: "#6ee7b7",
  },
}

const W = 1600, H = 1010
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

function draw(t) {
  const out = []
  const font = `font-family="Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"`
  const text = (x, y, s, { size = 15, weight = 400, fill = t.text, anchor = "start" } = {}) =>
    out.push(`<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" ${font}>${esc(s)}</text>`)
  const box = (x, y, w, h, [fill, stroke], { r = 12, dash = false, sw = 1.5 } = {}) =>
    out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"${dash ? ' stroke-dasharray="6 5"' : ""}/>`)
  // A node: title + up to two detail lines, centred.
  const node = (x, y, w, h, kind, title, lines = [], tag, tagLeft = false) => {
    const colors = { code: t.code, ai: t.ai, person: t.person, danger: t.danger }[kind]
    box(x, y, w, h, colors, { sw: kind === "code" ? 1.5 : 2 })
    const titleFill = { code: t.text, ai: t.aiText, person: t.personText, danger: t.dangerText }[kind]
    const total = 20 + lines.length * 18
    let cy = y + h / 2 - total / 2 + 15
    text(x + w / 2, cy, title, { size: 16, weight: 700, fill: titleFill, anchor: "middle" })
    for (const line of lines) { cy += 19; text(x + w / 2, cy, line, { size: 13, fill: t.muted, anchor: "middle" }) }
    if (tag) {
      const tw = tag.length * 7 + 16
      const tx = tagLeft ? x + 8 : x + w - tw - 8
      box(tx, y - 11, tw, 22, [t.bg, colors[1]], { r: 11, sw: 1.2 })
      text(tx + tw / 2, y + 4, tag, { size: 11.5, weight: 700, fill: titleFill, anchor: "middle" })
    }
  }
  const arrow = (d, { label, lx, ly, dash = false, color = t.arrow, anchor = "middle" } = {}) => {
    out.push(`<path d="${d}" fill="none" stroke="${color}" stroke-width="2"${dash ? ' stroke-dasharray="6 5"' : ""} marker-end="url(#arrow-${color.slice(1)})"/>`)
    if (label) text(lx, ly, label, { size: 12.5, fill: t.faint, anchor, weight: 600 })
  }
  const markers = [t.arrow, t.danger[1], t.person[1], t.ai[1]].map((c) =>
    `<marker id="arrow-${c.slice(1)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${c}"/></marker>`).join("")

  out.push(`<rect width="${W}" height="${H}" fill="${t.bg}"/>`)
  out.push(`<defs>${markers}</defs>`)

  // Title and legend
  text(40, 56, "FacilityOps AI · agent orchestration", { size: 28, weight: 800 })
  text(40, 86, "Code controls the flow · AI agents advise · people approve anything with consequences", { size: 16, fill: t.muted })
  const legend = [["code", "Code (deterministic)"], ["ai", "AI agent"], ["person", "Person"]]
  let lx = 1060
  for (const [kind, label] of legend) {
    const c = { code: t.code, ai: t.ai, person: t.person }[kind]
    box(lx, 44, 22, 22, c, { r: 6, sw: 2 })
    text(lx + 32, 61, label, { size: 14, fill: t.muted })
    lx += label.length * 8 + 70
  }

  // ---- Tier 1 ----
  box(40, 116, 1520, 230, [t.panel, t.panelStroke], { r: 18 })
  text(64, 148, "1  Intake workflow", { size: 18, weight: 800 })
  text(240, 148, "every request · IntakeCoordinator (workflow.py) runs the steps in a fixed order", { size: 14, fill: t.faint })

  const y1 = 178, h1 = 86
  node(60, y1, 150, h1, "code", "Request", ["text · photo", "voice · scan"])
  node(232, y1, 170, h1, "code", "Emergency screen", ["local rules, no AI", "runs first"])
  node(424, y1, 176, h1, "code", "File & image", ["bytes match type", "Content Safety"])
  node(622, y1, 170, h1, "code", "PII redaction", ["Azure AI Language", "before any model"])
  node(814, y1, 196, h1, "code", "Safety checks", ["Content Safety", "Prompt Shields"])
  node(1032, y1, 196, h1, "ai", "Classifier agent", ["category · priority", "no tools · schema"], "AGENT 1")
  node(1250, y1, 150, h1, "code", "Routing rules", ["thresholds in", "code, not AI"])
  for (const [a, b] of [[210, 232], [402, 424], [600, 622], [792, 814], [1010, 1032], [1228, 1250]])
    arrow(`M${a} ${y1 + h1 / 2} L${b - 4} ${y1 + h1 / 2}`)
  // outcomes
  node(1420, 168, 124, 48, "code", "Work order", [])
  node(1420, 228, 124, 48, "person", "Review queue", [])
  arrow(`M1400 ${y1 + 30} L1416 192`)
  arrow(`M1400 ${y1 + 56} L1416 252`)
  // emergency
  node(232, 284, 170, 46, "danger", "On-call person", [])
  arrow(`M317 ${y1 + h1} L317 280`, { color: t.danger[1] })
  text(412, 312, "gas · fire · flood: straight to a person, no AI", { size: 12.5, fill: t.dangerText, weight: 600 })

  // escalation arrow to tier 2
  arrow(`M1325 ${y1 + h1} L1325 300 Q1325 372 1230 372 L620 372 Q585 372 585 410 L585 436`, {
    label: "escalate: priority ≥ 4 in HVAC · electrical · plumbing (or a manager)  →  event in the outbox  →  incident worker",
    lx: 960, ly: 362,
  })

  // ---- Tier 2 ----
  box(40, 440, 1090, 440, [t.panel, t.panelStroke], { r: 18 })
  text(64, 472, "2  Incident team", { size: 18, weight: 800 })
  text(226, 472, "escalated incidents only · Magentic orchestration (Microsoft Agent Framework)", { size: 14, fill: t.faint })

  const sw = 300, sh = 82, gx = [80, 435, 790]
  const specialists = [
    ["Diagnostician", "equipment manuals", "Foundry IQ knowledge base"],
    ["Asset historian", "asset & maintenance", "history tools"],
    ["Safety & compliance", "procedures · permits", "can block the plan"],
    ["Procurement", "parts · stock", "vendor terms · cost"],
    ["Scheduler", "technicians: skills", "and availability"],
    ["Communicator", "drafts the tenant notice", "no tools · can't send"],
  ]
  const top = 498, bottom = 746
  specialists.forEach(([name, a, b], i) => {
    const x = gx[i % 3], y = i < 3 ? top : bottom
    node(x, y, sw, sh, "ai", name, [a, b], `AGENT ${i + 3}`)
  })
  // manager
  const mx = 390, my = 614, mw = 390, mh = 104
  node(mx, my, mw, mh, "ai", "Incident Manager", ["plans · picks the next specialist · re-plans", "task ledger + progress ledger · larger model"], "AGENT 2", true)
  for (let i = 0; i < 3; i++) {
    const cx = gx[i] + sw / 2
    arrow(`M${mx + mw / 2 + (i - 1) * 110} ${my} L${cx} ${top + sh + 4}`, { color: t.ai[1] })
    arrow(`M${mx + mw / 2 + (i - 1) * 110} ${my + mh} L${cx} ${bottom - 4}`, { color: t.ai[1] })
  }
  text(64, 862, "Budgets per run: ≤ 10 rounds · ≤ 60k tokens · ≤ 5 tool calls per specialist · ≤ 120 s   ·   read-only tools · redacted text only · small models for specialists", { size: 13, fill: t.faint, weight: 600 })

  // ---- Right column: plan, person, application ----
  node(1170, 470, 390, 96, "ai", "Incident plan", ["claims with citations · proposed actions", "flags: safety block · high cost · unverified"])
  arrow(`M1130 518 L1166 518`, { color: t.ai[1] })
  node(1170, 610, 390, 116, "person", "Facility manager", ["approve · revise · reject", "safety block can't be approved (code)", "high cost needs confirmation"], "PERSON")
  arrow(`M1365 566 L1365 606`)
  node(1170, 770, 390, 92, "code", "Application acts", ["only approved actions", "dispatch · parts · tenant notice"])
  arrow(`M1365 726 L1365 766`)
  // revise loop
  arrow(`M1170 700 L1136 700`, { color: t.person[1], dash: true })
  text(1098, 696, "revise", { size: 12.5, fill: t.personText, anchor: "end", weight: 700 })
  text(1098, 712, "team again", { size: 12.5, fill: t.personText, anchor: "end", weight: 700 })

  // ---- Bottom band ----
  box(40, 910, 1520, 66, t.band, { r: 14, sw: 1.5 })
  text(800, 940, "Every step above is recorded: decision trail · model input and output · append-only audit · traces", { size: 16, weight: 700, fill: t.bandText, anchor: "middle" })
  text(800, 962, "a facility manager can open “Why this decision” for any request", { size: 13, fill: t.bandText, anchor: "middle" })

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${out.join("")}</svg>`
}

for (const [name, theme] of Object.entries(THEMES)) fs.writeFileSync(`orchestration-${name}.svg`, draw(theme))
console.log("svg written")
