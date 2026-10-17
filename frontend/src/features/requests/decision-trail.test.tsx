import { TransportProvider } from "@connectrpc/connect-query"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { DecisionTrailCard } from "@/features/requests/decision-trail"
import { IntakeService } from "@/gen/facilityops/v1/intake_pb"
import { fakeBackend } from "@/test/fake-backend"

function renderCard(contentIncluded: boolean, calls: string[] = []) {
  const transport = fakeBackend("facility_manager", (router) =>
    router.service(IntakeService, {
      getDecisionTrail: (request) => {
        calls.push(request.requestId)
        return {
          requestId: request.requestId,
          steps: [
            { step: "intake.emergency_screen", outcome: "passed", service: "local rules" },
            {
              step: "intake.content_safety",
              outcome: "flagged",
              reasonCode: "document_attack_detected",
              service: "Azure AI Content Safety",
              version: "Prompt Shields 2024-09-01",
              durationMs: 180,
              details: { document_attacks: [true] },
            },
          ],
          modelCalls: [
            {
              id: "run-1",
              agentName: "Maintenance Triage Classifier",
              model: "gpt-5.2",
              promptSha: "2f091038960e",
              status: "succeeded",
              durationMs: 900,
              inputTokens: 300,
              outputTokens: 40,
              inputText: contentIncluded ? "[user] [PII_0001] says the tap drips" : "",
              outputText: contentIncluded ? '{"category":"plumbing"}' : "",
            },
          ],
          contentIncluded,
          contentRetentionDays: 90,
        }
      },
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TransportProvider transport={transport}>
        <DecisionTrailCard requestId="request-1" />
      </TransportProvider>
    </QueryClientProvider>,
  )
}

describe("DecisionTrailCard", () => {
  it("loads only when asked, then shows each step and what the model was sent", async () => {
    const calls: string[] = []
    renderCard(true, calls)
    expect(calls).toHaveLength(0) // viewing is audited, so nothing loads by itself

    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Show the decision trail" }))
    expect(await screen.findByText("Content Safety and Prompt Shields")).toBeInTheDocument()
    expect(screen.getByText("Hidden instructions found in the attachment")).toBeInTheDocument()
    expect(screen.getByText(/model gpt-5.2 · prompt 2f091038960e/)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /What was sent and what it answered/ }))
    expect(screen.getByText("[user] [PII_0001] says the tap drips")).toBeInTheDocument()
    expect(screen.getByText(/"category": "plumbing"/)).toBeInTheDocument()
    expect(calls).toEqual(["request-1"])
  })

  it("explains who can see model content when it isn't included", async () => {
    renderCard(false)
    await userEvent.setup().click(screen.getByRole("button", { name: "Show the decision trail" }))
    expect(await screen.findByText(/visible to facility managers/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /What was sent/ })).not.toBeInTheDocument()
  })
})
