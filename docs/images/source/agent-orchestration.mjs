// Source of the agent orchestration images in docs/images:
//   agent-orchestration-{light,dark}.png   the static diagram
//   agent-orchestration-{light,dark}.gif   the same diagram, animated step by step with captions
//
//   node agent-orchestration.mjs            # writes the SVGs (static + one per animation frame) to ./out
//   ./render.sh                             # renders them with headless Chromium and builds the PNGs and GIFs
//
// Edit the layout and the storyboard here, never the images.

import fs from "node:fs"

const THEMES = {
  light: {
    bg: "#ffffff", panel: "#f8fafc", panelStroke: "#cbd5e1", text: "#0f172a", muted: "#475569", faint: "#64748b",
    code: ["#f1f5f9", "#64748b"], ai: ["#f3e8ff", "#7c3aed"], person: ["#fef3c7", "#b45309"],
    danger: ["#fee2e2", "#dc2626"], arrow: "#475569", aiText: "#5b21b6", personText: "#92400e", dangerText: "#991b1b",
    band: ["#ecfdf5", "#059669"], bandText: "#065f46", dot: "#2563eb", glow: "#2563eb", caption: ["#eff6ff", "#2563eb"],
  },
  dark: {
    bg: "#0d1117", panel: "#161b22", panelStroke: "#30363d", text: "#e6edf3", muted: "#9da7b3", faint: "#8b949e",
    code: ["#1f2630", "#8b949e"], ai: ["#2a1f4d", "#a78bfa"], person: ["#3a2a0a", "#f59e0b"],
    danger: ["#3b1219", "#f87171"], arrow: "#9da7b3", aiText: "#c4b5fd", personText: "#fcd34d", dangerText: "#fca5a5",
    band: ["#0f2a22", "#34d399"], bandText: "#6ee7b7", dot: "#60a5fa", glow: "#60a5fa", caption: ["#0c1a2e", "#60a5fa"],
  },
}

const W = 1600, H = 1010, CAPTION_H = 110
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
const SPECIALISTS = [
  ["Diagnostician", "equipment manuals", "Foundry IQ knowledge base"],
  ["Asset historian", "asset & maintenance", "history tools"],
  ["Safety & compliance", "procedures · permits", "can block the plan"],
  ["Procurement", "parts · stock", "vendor terms · cost"],
  ["Scheduler", "technicians: skills", "and availability"],
  ["Communicator", "drafts the tenant notice", "no tools · can't send"],
]

// ---- The storyboard: what each animation step highlights, where the dot travels, and what it says ----
const tier1 = ["request", "a_req_em", "emergency", "a_em_file", "file", "a_file_pii", "pii", "a_pii_safe", "safety", "a_safe_cls", "classifier", "a_cls_route", "routing"]
const SPECIALIST_ROUNDS = [
  [1, "Round 1 · the asset historian finds the boiler and its history: the same E04 fault 12 days ago, a repeat failure."],
  [0, "Round 2 · the diagnostician searches the equipment manuals in the Foundry IQ knowledge base and cites section 6.2."],
  [2, "Round 3 · safety & compliance checks permits and lockout rules. An expired gas permit means the work must be blocked."],
  [3, "Round 4 · procurement checks parts, stock and vendor terms, and prices the work. Every tool is read-only."],
  [4, "Round 5 · the scheduler finds a qualified technician by skill and availability, by id only, never by name."],
  [5, "Round 6 · the communicator drafts a short notice for the tenant, in English. It can't send anything."],
]
const STORY = [
  { focus: ["request"], caption: "A tenant reports a problem: a text, a photo, a voice note or a scanned document." },
  { focus: ["request", "a_req_em", "emergency", "a_em_oncall", "oncall"], path: ["a_req_em", "a_em_oncall"],
    caption: "Local rules check for gas, fire or flooding first. An emergency goes straight to an on-call person, with no AI involved." },
  { focus: ["emergency", "a_em_file", "file", "a_file_pii", "pii", "a_pii_safe", "safety"], path: ["a_em_file", "a_file_pii", "a_pii_safe"],
    caption: "Files are checked by their bytes, images by Content Safety. Personal details become placeholders like [PII_0001] before any model sees the text." },
  { focus: ["safety", "a_safe_cls", "classifier"], path: ["a_safe_cls"],
    caption: "Agent 1, the classifier, suggests a category and a priority. It has no tools and must answer in a fixed JSON schema." },
  { focus: ["classifier", "a_cls_route", "routing", "a_route_wo", "workorder", "a_route_rev", "review"], path: ["a_cls_route", "a_route_wo"],
    caption: "Code, not AI, picks the route from confidence and priority: an automatic work order, or a person in the review queue." },
  { focus: ["routing", "a_escalate", "manager"], path: ["a_escalate"],
    caption: "High-priority heating, electrical or plumbing problems are escalated: an event wakes the incident worker, off the request path." },
  { focus: ["manager"], caption: "Agent 2, the Incident Manager, writes a plan and, every round, decides which specialist works next." },
  ...SPECIALIST_ROUNDS.map(([i, caption]) => ({ focus: ["manager", `a_mgr_sp${i}`, `sp${i}`], path: [`a_mgr_sp${i}`], caption })),
  { focus: ["manager", "a_team_plan", "plan"], path: ["a_team_plan"],
    caption: "The result is an incident plan: claims with citations, proposed actions, and flags such as safety block or high cost." },
  { focus: ["plan", "a_plan_fm", "fm", "a_revise"], path: ["a_plan_fm"],
    caption: "A facility manager approves, asks for a revision or rejects. Code refuses a safety-blocked plan; a high cost must be confirmed." },
  { focus: ["fm", "a_fm_app", "app"], path: ["a_fm_app"],
    caption: "Only the application acts on an approved plan: it assigns the technician, raises purchase requests, translates the notice." },
  { focus: ["trail"], caption: "Every step is recorded: the decision trail, what each model was sent and answered, an append-only audit, traces." },
  { focus: null, caption: "Code controls the flow · AI agents advise · people approve anything with consequences." },
]

function draw(t, { focus = null, dot = null, caption = null, step = 0 } = {}) {
  const out = []
  const paths = {}
  const font = `font-family="Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"`
  const on = (id) => !focus || focus.includes(id)
  let open = null
  const group = (id, fn) => {
    const lit = on(id)
    out.push(`<g opacity="${lit ? 1 : 0.18}">`)
    open = lit && focus ? id : null
    fn()
    open = null
    out.push(`</g>`)
  }
  const text = (x, y, s, { size = 15, weight = 400, fill = t.text, anchor = "start" } = {}) =>
    out.push(`<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" ${font}>${esc(s)}</text>`)
  const box = (x, y, w, h, [fill, stroke], { r = 12, dash = false, sw = 1.5 } = {}) =>
    out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"${dash ? ' stroke-dasharray="6 5"' : ""}/>`)
  // A node: title + up to two detail lines, centred. A highlighted node gets a glow ring.
  const node = (id, x, y, w, h, kind, title, lines = [], tag, tagLeft = false) => group(id, () => {
    const colors = { code: t.code, ai: t.ai, person: t.person, danger: t.danger }[kind]
    if (open) out.push(`<rect x="${x - 6}" y="${y - 6}" width="${w + 12}" height="${h + 12}" rx="16" fill="none" stroke="${t.glow}" stroke-width="3" opacity="0.55"/>`)
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
  })
  const arrow = (id, d, { label, lx, ly, dash = false, color = t.arrow, anchor = "middle" } = {}) => group(id, () => {
    paths[id] = d
    const c = open ? t.dot : color
    out.push(`<path d="${d}" fill="none" stroke="${c}" stroke-width="${open ? 3 : 2}"${dash ? ' stroke-dasharray="6 5"' : ""} marker-end="url(#arrow-${c.slice(1)})"/>`)
    if (label) text(lx, ly, label, { size: 12.5, fill: t.faint, anchor, weight: 600 })
  })
  const markers = [t.arrow, t.danger[1], t.person[1], t.ai[1], t.dot].map((c) =>
    `<marker id="arrow-${c.slice(1)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${c}"/></marker>`).join("")

  const height = caption ? H + CAPTION_H : H
  out.push(`<rect width="${W}" height="${height}" fill="${t.bg}"/>`)
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
  node("request", 60, y1, 150, h1, "code", "Request", ["text · photo", "voice · scan"])
  node("emergency", 232, y1, 170, h1, "code", "Emergency screen", ["local rules, no AI", "runs first"])
  node("file", 424, y1, 176, h1, "code", "File & image", ["bytes match type", "Content Safety"])
  node("pii", 622, y1, 170, h1, "code", "PII redaction", ["Azure AI Language", "before any model"])
  node("safety", 814, y1, 196, h1, "code", "Safety checks", ["Content Safety", "Prompt Shields"])
  node("classifier", 1032, y1, 196, h1, "ai", "Classifier agent", ["category · priority", "no tools · schema"], "AGENT 1")
  node("routing", 1250, y1, 150, h1, "code", "Routing rules", ["thresholds in", "code, not AI"])
  const links = [["a_req_em", 210, 232], ["a_em_file", 402, 424], ["a_file_pii", 600, 622], ["a_pii_safe", 792, 814], ["a_safe_cls", 1010, 1032], ["a_cls_route", 1228, 1250]]
  for (const [id, a, b] of links) arrow(id, `M${a} ${y1 + h1 / 2} L${b - 4} ${y1 + h1 / 2}`)
  // outcomes
  node("workorder", 1420, 168, 124, 48, "code", "Work order", [])
  node("review", 1420, 228, 124, 48, "person", "Review queue", [])
  arrow("a_route_wo", `M1400 ${y1 + 30} L1416 192`)
  arrow("a_route_rev", `M1400 ${y1 + 56} L1416 252`)
  // emergency
  node("oncall", 232, 284, 170, 46, "danger", "On-call person", [])
  arrow("a_em_oncall", `M317 ${y1 + h1} L317 280`, { color: t.danger[1] })
  group("oncall", () => text(412, 312, "gas · fire · flood: straight to a person, no AI", { size: 12.5, fill: t.dangerText, weight: 600 }))

  // escalation arrow to tier 2
  arrow("a_escalate", `M1325 ${y1 + h1} L1325 300 Q1325 372 1230 372 L620 372 Q585 372 585 410 L585 436`, {
    label: "escalate: priority ≥ 4 in HVAC · electrical · plumbing (or a manager)  →  event in the outbox  →  incident worker",
    lx: 960, ly: 362,
  })

  // ---- Tier 2 ----
  box(40, 440, 1090, 440, [t.panel, t.panelStroke], { r: 18 })
  text(64, 472, "2  Incident team", { size: 18, weight: 800 })
  text(226, 472, "escalated incidents only · Magentic orchestration (Microsoft Agent Framework)", { size: 14, fill: t.faint })

  const sw = 300, sh = 82, gx = [80, 435, 790]
  const top = 498, bottom = 746
  const mx = 390, my = 614, mw = 390, mh = 104
  SPECIALISTS.forEach(([name, a, b], i) => {
    const x = gx[i % 3], y = i < 3 ? top : bottom
    node(`sp${i}`, x, y, sw, sh, "ai", name, [a, b], `AGENT ${i + 3}`)
    const cx = x + sw / 2, fromX = mx + mw / 2 + ((i % 3) - 1) * 110
    arrow(`a_mgr_sp${i}`, i < 3 ? `M${fromX} ${my} L${cx} ${top + sh + 4}` : `M${fromX} ${my + mh} L${cx} ${bottom - 4}`, { color: t.ai[1] })
  })
  node("manager", mx, my, mw, mh, "ai", "Incident Manager", ["plans · picks the next specialist · re-plans", "task ledger + progress ledger · larger model"], "AGENT 2", true)
  text(64, 862, "Budgets per run: ≤ 10 rounds · ≤ 250k tokens · ≤ 5 tool calls per specialist · ≤ 240 s   ·   read-only tools · redacted text only · small models for specialists", { size: 13, fill: t.faint, weight: 600 })

  // ---- Right column: plan, person, application ----
  node("plan", 1170, 470, 390, 96, "ai", "Incident plan", ["claims with citations · proposed actions", "flags: safety block · high cost · unverified"])
  arrow("a_team_plan", `M1130 518 L1166 518`, { color: t.ai[1] })
  node("fm", 1170, 610, 390, 116, "person", "Facility manager", ["approve · revise · reject", "safety block can't be approved (code)", "high cost needs confirmation"], "PERSON")
  arrow("a_plan_fm", `M1365 566 L1365 606`)
  node("app", 1170, 770, 390, 92, "code", "Application acts", ["only approved actions: assign · parts", "translated tenant notice (Translator)"])
  arrow("a_fm_app", `M1365 726 L1365 766`)
  // revise loop
  group("a_revise", () => {
    arrow("a_revise_line", `M1170 700 L1136 700`, { color: t.person[1], dash: true })
    text(1098, 696, "revise", { size: 12.5, fill: t.personText, anchor: "end", weight: 700 })
    text(1098, 712, "team again", { size: 12.5, fill: t.personText, anchor: "end", weight: 700 })
  })

  // ---- Bottom band ----
  group("trail", () => {
    if (open) out.push(`<rect x="34" y="904" width="1532" height="78" rx="18" fill="none" stroke="${t.glow}" stroke-width="3" opacity="0.55"/>`)
    box(40, 910, 1520, 66, t.band, { r: 14, sw: 1.5 })
    text(800, 940, "Every step above is recorded: decision trail · model input and output · append-only audit · traces", { size: 16, weight: 700, fill: t.bandText, anchor: "middle" })
    text(800, 962, "a facility manager can open “Why this decision” for any request", { size: 13, fill: t.bandText, anchor: "middle" })
  })

  if (dot) out.push(`<circle cx="${dot.x}" cy="${dot.y}" r="9" fill="${t.dot}" stroke="${t.bg}" stroke-width="3"/>`)

  if (caption) {
    box(40, H + 6, W - 80, CAPTION_H - 24, t.caption, { r: 14, sw: 2 })
    const badge = `${step} / ${STORY.length}`
    box(64, H + 31, 76, 36, [t.caption[1], t.caption[1]], { r: 18, sw: 1 })
    text(102, H + 55, badge, { size: 16, weight: 800, fill: t.bg, anchor: "middle" })
    text(162, H + 56, caption, { size: 19, weight: 600, fill: t.text })
  }
  return { svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${height}" viewBox="0 0 ${W} ${height}">${out.join("")}</svg>`, paths }
}

// ---- Points along "M x y L x y Q cx cy x y ..." paths, for the moving dot ----
function sample(d, steps) {
  const tokens = d.match(/[MLQ]|-?\d+(\.\d+)?/g)
  const segments = []
  let i = 0, cur = null
  while (i < tokens.length) {
    const cmd = tokens[i++]
    const num = () => Number(tokens[i++])
    if (cmd === "M") cur = [num(), num()]
    else if (cmd === "L") { const p = [num(), num()]; segments.push((t) => [cur[0] + (p[0] - cur[0]) * t, cur[1] + (p[1] - cur[1]) * t]); cur = p }
    else if (cmd === "Q") {
      const c = [num(), num()], p = [num(), num()], s = cur
      segments.push((t) => [(1 - t) ** 2 * s[0] + 2 * (1 - t) * t * c[0] + t * t * p[0], (1 - t) ** 2 * s[1] + 2 * (1 - t) * t * c[1] + t * t * p[1]])
      cur = p
    }
  }
  const points = []
  for (const seg of segments) for (let k = 0; k <= 40; k++) points.push(seg(k / 40))
  // Even spacing by distance, so the dot moves at a steady speed.
  const dist = [0]
  for (let k = 1; k < points.length; k++) dist.push(dist[k - 1] + Math.hypot(points[k][0] - points[k - 1][0], points[k][1] - points[k - 1][1]))
  const total = dist[dist.length - 1]
  return Array.from({ length: steps }, (_, s) => {
    const target = (total * s) / (steps - 1)
    const k = dist.findIndex((v) => v >= target)
    return { x: points[Math.max(k, 0)][0], y: points[Math.max(k, 0)][1] }
  })
}

const DOT_FRAMES = 10, DOT_MS = 70, HOLD_MS = 3200, LAST_HOLD_MS = 5000
fs.mkdirSync("out", { recursive: true })
for (const [name, theme] of Object.entries(THEMES)) {
  const { svg, paths } = draw(theme)
  fs.writeFileSync(`out/static-${name}.svg`, svg)
  fs.writeFileSync(`agent-orchestration-${name}.svg`, svg)
  const frames = []
  STORY.forEach((s, index) => {
    const step = index + 1
    const dots = (s.path ?? []).flatMap((id) => sample(paths[id], DOT_FRAMES))
    dots.forEach((dot, k) => frames.push({ ...s, step, dot, ms: DOT_MS, file: `out/${name}-${String(step).padStart(2, "0")}-${String(k).padStart(2, "0")}.svg` }))
    frames.push({ ...s, step, dot: dots.at(-1) ?? null, ms: index === STORY.length - 1 ? LAST_HOLD_MS : HOLD_MS, file: `out/${name}-${String(step).padStart(2, "0")}-hold.svg` })
  })
  for (const f of frames) fs.writeFileSync(f.file, draw(theme, f).svg)
  // ffmpeg concat list: each frame with how long it shows.
  const list = frames.map((f) => `file '${f.file.replace("out/", "").replace(".svg", ".png")}'\nduration ${f.ms / 1000}`).join("\n")
  fs.writeFileSync(`out/${name}-frames.txt`, `${list}\nfile '${frames.at(-1).file.replace("out/", "").replace(".svg", ".png")}'\n`)
  console.log(`${name}: ${frames.length} frames`)
}
