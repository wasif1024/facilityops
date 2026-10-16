---
name: facilityops-pii-protection
description: "Use when implementing, reviewing, or testing PII detection, redaction, protected identity mappings, role-based reveal, logs, traces, analytics, or evaluation data in FacilityOps AI."
---

# FacilityOps PII Protection

## Invariants

- Use synthetic tenants, buildings, equipment, manuals, and requests only in the public repository.
- Keep redacted request text separate from any protected mapping needed to restore operational details.
- Never place raw PII or the protected mapping in model prompts, logs, traces, analytics, or evaluation datasets.

## Procedure

1. Identify PII sources and data flows, including text, OCR, transcripts, tool inputs, exceptions, and audit events.
2. Redact before any model call or general-purpose telemetry write. Preserve the redacted source as the default application and evaluation representation.
3. Store reversible mappings separately behind an explicit authorization boundary. Grant reveal access only to roles with a demonstrated operational need, and audit each reveal.
4. Apply least privilege by role. A technician may receive only the specific unit or location details needed for an assigned work order; analytics and evaluation paths never receive the mapping.
5. Handle detection failures conservatively: do not silently forward unredacted content. Route uncertain cases to a safe human-review path.
6. Avoid logging full request bodies in exceptions or debug output. Log stable identifiers, redaction status, and non-sensitive reason codes instead.
7. Test common identifiers, multiple identifiers in one request, multilingual and malformed inputs, false positives, mapping authorization, and telemetry leakage.

## Verification

- Search captured prompts, logs, traces, and evaluation fixtures for planted synthetic identifiers.
- Verify unauthorized roles cannot reveal mappings and authorized reveals are auditable.
- Verify redaction failures block model processing rather than leaking original content.