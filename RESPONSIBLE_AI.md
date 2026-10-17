# Responsible AI transparency note: FacilityOps AI

This note explains what the AI in FacilityOps AI does, what it doesn't do, how well it works, where it falls short,
and how people stay in control. It follows the structure of Microsoft's transparency notes. FacilityOps AI is a
**portfolio and teaching project that runs on synthetic data only**; it is not certified for real buildings.
Read it with the [specification](docs/specification.md), which holds the full evaluation results.

*Last reviewed: 2026-10-18.*

## What the system is

FacilityOps AI takes maintenance requests from residents (text, photo, voice note or scanned document) and helps
facility staff handle them. It uses AI in three places, always as an adviser:

| Where | AI | What it produces | Who or what decides |
|---|---|---|---|
| Every request | **Classifier** agent (gpt-5.2) | A category and a priority, with a confidence | Code routes the request using fixed thresholds; uncertain answers go to a person |
| High-priority equipment problems | **Incident team**: an Incident Manager (gpt-5.2) and six specialists (gpt-5.4-mini) | An incident plan: claims with citations, proposed actions, safety flags | A facility manager approves, revises or rejects it; only then does the application act |
| Notices to residents | **Communicator** agent | An English draft | A facility manager edits and approves it; the application translates it |

AI services also check and prepare the input: Azure AI Content Safety and Prompt Shields (harmful content, prompt
injection), Azure AI Language (personal data), Vision, Speech and Document Intelligence (reading attachments), and
Translator (translations for staff and residents).

## Intended uses

- Sorting routine maintenance requests so that clear cases become work orders quickly and the rest reach a person.
- Helping a facility manager investigate a complex equipment fault, with every factual claim traceable to a manual,
  procedure or record.
- Letting residents report in English, Spanish or French, and receive notices in their language.
- Teaching: showing how enterprise AI practices (guardrails, privacy, evaluation, human oversight, transparency)
  fit together in one working system.

## Uses it is not designed for

- **Emergencies.** Gas, fire, carbon monoxide, electrical hazards and flooding are detected by local rules **before
  any AI** and handed straight to an on-call person. The AI never decides whether something is an emergency, and an
  emergency can never be sent to the incident team.
- **Acting on its own.** No agent can create, assign, order or send anything. Agents have read-only tools; the
  application carries out actions only after a person approves them.
- **Decisions about people.** It does not assess residents or technicians. Technicians appear to agents only as ids
  and skills, residents only as placeholders such as `[PII_0001]`.
- **Real buildings without further validation.** The manuals, buildings, people and history are invented. A real
  deployment would need its own evaluation on its own data, a safety review, and local regulatory checks.

## How people stay in control

| Safeguard | How it works |
|---|---|
| Emergency bypass | Local rules run first; emergencies go to a person, never to a model |
| Routing in code | Category and priority are suggestions; code applies the confidence thresholds and sends uncertain, flagged or failed cases to the review queue (fail closed) |
| Approval for high priority | Priority 4 and 5 work orders wait for a facility manager |
| Plan approval gate in code | A plan with a **safety block** can't be approved; a plan above the **cost threshold** needs explicit confirmation; rejections use fixed reasons |
| The application acts, not the AI | After approval, the application assigns only an active technician account and prices parts from its own records, never from the model's estimate; anything it can't do is left for a person |
| Notices approved by a person | The resident sees only text a facility manager approved; notices with personal-data placeholders are refused |
| Budgets | Each incident run is capped at 10 rounds, 250k tokens, 240 seconds and 5 tool calls per specialist; a run that hits a limit goes to a person |

## Privacy

- **Redaction before any model.** Personal details are replaced with placeholders by Azure AI Language before
  classification, translation or the incident team see the text. Logs, traces, analytics and evaluation data never
  hold personal data.
- **Protected reveal.** The original values are encrypted (the key is in Key Vault in Azure) and can be revealed only
  by roles that need them, for example a technician on their own work order. Every reveal is audited.
- **No raw media kept.** Photos, voice notes and documents are read in memory and discarded; only the redacted text
  is stored.
- **What models were sent is kept for transparency, not training.** The redacted input and output of each model call
  is stored for 90 days (configurable), readable only by staff with the right permission, with each view audited.
  The incident team's agents run with `store: false`, so the model service keeps no copy of their conversations.

## Transparency

- **Decision trail.** Every request has a step-by-step record: each check and its result, each model call (agent,
  model, prompt version, tokens), each tool call, every human decision and every action the application took.
  Facility managers open it as "Why this decision" on the request page.
- **Machine translation is labelled.** Staff and residents always see that a text was machine-translated, with the
  original one click away.
- **Unverified claims are marked.** A claim in an incident plan without a citation is shown as unverified; a citation
  that no tool returned in that run is removed.
- **Append-only audit.** Audit and trail tables can't be edited or deleted except by the retention job.

## Performance

Measured on a synthetic golden set of 108 requests, two consecutive live runs (2026-10-17), each case classified five
times. Full method and results: [specification §10](docs/specification.md#10-evaluation-and-acceptance).

| Metric | Result | Target |
|---|---|---|
| Emergency recall (local rules) | 1.00 | 1.00, hard gate on every pull request |
| Category accuracy | 0.91 | ≥ 0.85 |
| Priority within ±1 | 0.94 | ≥ 0.90 |
| Ambiguous requests sent to a person | 1.00 | 1.00 |
| Prompt-injection resistance | 1.00 | 1.00 |
| Same route for the same request with a different name, language or building | 1.00 | 1.00 |
| Requests automated · accuracy of those | 0.90 · 0.96, 0.91 · 0.94 | tracked |

Media intake (scans and photos through the real services) passed 6 of 6 checks, including a planted name and phone
number that must not survive redaction and a hidden instruction inside a scan that must be caught.

The incident team passes 9 scripted scenarios with 5 hard gates on every pull request (no action before approval,
emergencies never enter, events hold ids only, no incident left stuck, a complete trail). Those test the machinery,
not model quality: see the limitations below.

## Known limitations

- **A language effect on priority.** In one fairness group (a running toilet), the French variant was rated priority
  3 every time while the English variants were mostly 2. It stays within the same route, so the resident is treated
  the same way and only the queue order differs. Classifying an English translation first does not remove it: the
  translation keeps the wording ("running continuously") that seems to drive it. Fixing it needs more multilingual
  test cases, not a prompt tweak fitted to one example.
- **Incident plan quality is not yet measured at scale.** The live evaluation (groundedness and tool-call accuracy
  with Foundry evaluators, safety-block recall) is built but has not been run in full, to limit model costs. In
  individual live runs the team correctly blocked an unsafe boiler repair and produced fully cited plans, but that is
  evidence, not a measurement.
- **Not red-teamed yet.** An automated red-team scan (Azure AI Red Teaming Agent, PyRIT) is planned; until it is run
  and reported, resistance to attacks beyond the golden set's injection cases is unmeasured.
- **Emergency rules are rules.** They catch the phrasings in the golden set (including indirect ones such as
  "smells like rotten eggs"); an unusual description could be missed. A missed emergency still goes through
  classification and, if rated high priority, waits for a person, but it would not reach on-call immediately.
- **Models are not deterministic.** The same request can get a different answer. The confirmation policy (classify
  again and require agreement when confidence is borderline) reduces this; about 11–17% of identical requests can
  still take a different route between runs, which is why uncertain cases go to a person.
- **Synthetic data.** Every result above is on invented requests, buildings and manuals.

## Evaluation and testing

| What | How | When |
|---|---|---|
| Golden set (classification, fairness, injection, emergencies) | `python -m evals.run_eval` | Offline on every pull request (emergency recall is a hard gate); live on demand |
| Media intake | `python -m evals.run_media_eval` | On demand (live) |
| Incident scenarios, scripted | `python -m evals.run_incident_scenarios` | Every pull request |
| Incident scenarios, live (Foundry evaluators) | `python -m evals.run_incident_scenarios --mode live` | On demand; not yet run in full |
| Red-team scan | Azure AI Red Teaming Agent | Planned |

## Reporting a problem

This is an open-source teaching project. If you find a behaviour that is unsafe, unfair or misleading, please open
an issue with the request text (synthetic only, never real personal data), what happened and what you expected.
