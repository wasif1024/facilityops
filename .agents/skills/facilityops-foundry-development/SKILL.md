---
name: facilityops-foundry-development
description: "Use when implementing, changing, or reviewing FacilityOps AI code that uses Microsoft Foundry, Microsoft Agent Framework, Entra ID, or Azure AI services. Covers current API research, project reuse, secure configuration, bounded agent execution, and observability."
---

# FacilityOps Foundry Development

## Scope

Use this skill for implementation work involving Microsoft Foundry or Microsoft Agent Framework in this repository. Follow the project's `CLAUDE.md` and preserve its existing architecture and environment assumptions.

## Procedure

1. Inspect the current project files and identify the owning workflow or agent before proposing code. Do not assume a scaffold, SDK version, endpoint, or resource exists.
2. Before using Foundry or Agent Framework APIs, consult current Microsoft documentation and examples for the installed or intended SDK version. These APIs change quickly; do not rely on remembered signatures.
3. Reuse the existing Foundry project and subscription. Do not create resources, deploy models, or change Azure configuration unless the user asks.
4. Use Entra ID with `DefaultAzureCredential`. Read the project endpoint from environment configuration; never add secrets, keys, or real endpoints to tracked files.
5. Keep intake orchestration deterministic. Use agents only for bounded tasks that need language understanding or planning, and cap turns, tokens, tool calls, and elapsed time.
6. Require explicit human approval for safety-critical or above-threshold-cost actions. Agent suggestions are not authorization to execute those actions.
7. Instrument meaningful workflow and tool boundaries with OpenTelemetry-compatible traces while excluding raw PII, protected mappings, credentials, and sensitive request content.
8. Make changes consistent with the repository's Python conventions and add focused tests for behavior and failure paths.

## Completion Checks

- SDK calls match current official documentation and the project's dependency versions.
- Configuration contains no secrets or committed endpoint values.
- Agent loops and tool access are bounded, and safety-sensitive actions retain human oversight.
- Tests cover the changed behavior without using real tenant, building, or maintenance data.