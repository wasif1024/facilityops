# Screenshots

One web app, four workspaces: each person sees only what their role allows. Screenshots use synthetic data only
(`python scripts/seed_demo.py`, plus incidents from live runs of the AI incident team on the synthetic facility
world); every name, building and manual is invented. For a 70-second walkthrough, see the demo at the top of the
[README](../README.md).

## Tenant: report a problem and follow it

Report by text, photo, voice note or a scanned letter or form, in English, Spanish or French. Emergencies (gas,
carbon monoxide, fire, electrical hazards, flooding) skip the AI and go straight to the on-call team. Uploads are checked by their bytes, pictures by
Content Safety, and text found in a photo or scan by Prompt Shields as a document, so an instruction hidden in a
scan can't steer the AI. Names and phone numbers are redacted before any AI sees them, shown here as "personal
detail".

| Report a problem | My requests |
|---|---|
| ![Report a problem](images/tenant-report.png) | ![My requests](images/tenant-my-requests.png) |

<p align="center">
  <img src="images/tenant-mobile-voice.png" alt="Recording a voice note on a phone" width="300">
  <img src="images/tenant-mobile-document.png" alt="Sending a scanned form from a phone" width="300">
</p>

Rules can miss an unusual description of an emergency, so a tenant can also say so directly: ticking **This is an
emergency** sends the report to the on-call team at once, before any check or AI, even with no text. Notices from
facility management appear on the request; a tenant who wrote in Spanish or French reads them machine-translated
into their language, with the approved English one click away.

| Declaring an emergency | A notice from facility management |
|---|---|
| ![The report form with "This is an emergency" ticked and a red "Send to the on-call team now" button](images/tenant-emergency.png) | ![A tenant's request with a message from facility management](images/tenant-notice.png) |

## Facility manager: review queue, approvals and assignment

Everything the workflow won't decide alone lands in the review queue, emergencies first, with the reason in plain
words. Managers approve high-priority work, assign technicians, and can reveal the redacted details of a
request; every reveal is recorded in the audit log.

| Review queue | Work orders |
|---|---|
| ![Review queue](images/manager-review-queue.png) | ![Work orders](images/manager-work-orders.png) |

![Revealing personal details on a work order](images/manager-work-order-reveal.png)

### Requests in other languages

Requests written in Spanish or French reach staff with an English machine translation from Azure AI Translator,
clearly labelled, with the tenant's original one click away. Only redacted text is translated, so revealing
personal details works on the translation too.

![A French request shown to a facility manager in English, with a personal detail revealed](images/manager-translation.png)

## Facility manager: the AI incident team

High-priority equipment problems go to a team of AI agents that investigates with read-only tools and the
equipment manuals, then proposes a plan. Every claim cites the manual section, procedure or record it came from;
a claim without one is marked unverified. A plan with a safety block can't be approved: here the boiler's gas
work permit has expired, so code disables **Approve plan** and the manager can only revise or reject it.

![An incident plan with a safety block: the expired permit, the evidence with citations, and the proposed actions](images/manager-incident-plan.png)

After a revision is approved, the application (not the AI) carries out each action and shows what it did: the
lockout is recorded, the named technician is assigned, and the tenant notice, approved by the manager, is sent.

![An approved plan: lockout recorded, technician assigned, tenant notice sent, open questions and the notice text](images/manager-incident-actions.png)

### Why this decision

Every request has a decision trail: each check and its result, every model call with the exact (redacted) input
and output, every tool call and every human decision. Opening the model content is itself recorded in the audit
log.

<p align="center"><img src="images/decision-trail.png" alt="The decision trail of a request: emergency screen, PII redaction, Content Safety, classification and routing, each with its result" width="520"></p>

## Technician: my work orders

Technicians see only their own assignments, reveal details only for work assigned to them, and mark jobs done.

![Technician work orders](images/technician-work-orders.png)

## Administrator: access, quality and monitoring

Administrators manage users and roles but can't read personal data or approve work (separation of duties). They
track evaluation results over time and watch how the intake pipeline and its AI agents behave: counts and
timings only, never request content.

| Users and roles | Evaluations |
|---|---|
| ![Users](images/admin-users.png) | ![Evaluations](images/admin-evaluations.png) |

| Intake monitoring | Agent monitoring (dark mode) |
|---|---|
| ![Intake monitoring](images/admin-monitoring.png) | ![Agent monitoring](images/admin-monitoring-dark.png) |
