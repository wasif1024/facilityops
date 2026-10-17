import { create } from "@bufbuild/protobuf"
import { TransportProvider } from "@connectrpc/connect-query"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { IdentityProvider, SessionProvider } from "@/auth/session"
import { IncidentPanel } from "@/features/incidents/incident-panel"
import { NoticesCard } from "@/features/incidents/tenant-notices"
import { TenantNoticeSchema } from "@/gen/facilityops/v1/intake_pb"
import {
  type DecideIncidentPlanRequest,
  IncidentSchema,
  IncidentService,
  IncidentStatus,
  PlanDecision,
  PlanVersionSchema,
} from "@/gen/facilityops/v1/incidents_pb"
import { fakeBackend } from "@/test/fake-backend"

const incident = create(IncidentSchema, {
  id: "incident-1",
  requestId: "request-1",
  status: IncidentStatus.PLAN_READY,
  escalationReason: "high_priority_equipment",
})

function plan(flags: string[], safetyBlock = false) {
  return create(PlanVersionSchema, {
    id: "plan-1",
    version: 1,
    status: "proposed",
    summary: "Chiller CX90 compressor has failed; replace it.",
    flags,
    safetyBlock,
    safetyNotes: safetyBlock ? "No valid electrical isolation permit (permit:p-12 expired)." : "",
    estimatedTotalCost: 4850,
    claims: [
      { statement: "Compressor is out of stock.", specialist: "procurement", verified: true, citations: [{ sourceType: "record", sourceId: "part:cx90-compressor" }] },
      { statement: "The fault is probably electrical.", specialist: "diagnostician", verified: false },
    ],
    actions: [{ type: "order_part", description: "Order compressor CX90-C", estimatedCost: 4200, partSku: "cx90-compressor", quantity: 1 }],
    openQuestions: ["Is the vendor contract renewed?"],
  })
}

function renderPanel(flags: string[], { safetyBlock = false, role = "facility_manager" } = {}) {
  const decisions: DecideIncidentPlanRequest[] = []
  const transport = fakeBackend(role, (router) =>
    router.service(IncidentService, {
      listIncidents: () => ({ incidents: [incident], total: 1 }),
      getIncident: () => ({ incident, plans: [plan(flags, safetyBlock)], actions: [] }),
      decideIncidentPlan: (request) => {
        decisions.push(request)
        return { incident: { ...incident, status: IncidentStatus.APPROVED }, plans: [] }
      },
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TransportProvider transport={transport}>
        <IdentityProvider initialDevUser={role}>
          <SessionProvider>
            <IncidentPanel requestId="request-1" />
          </SessionProvider>
        </IdentityProvider>
      </TransportProvider>
    </QueryClientProvider>,
  )
  return decisions
}

describe("IncidentPanel", () => {
  it("shows the evidence, marks unverified claims, and needs the cost confirmed to approve", async () => {
    const decisions = renderPanel(["high_cost", "unverified_claims"])

    expect(await screen.findByText("Chiller CX90 compressor has failed; replace it.")).toBeInTheDocument()
    expect(screen.getByText(/part:cx90-compressor/)).toBeInTheDocument()
    expect(screen.getByText("Unverified: no source cited")).toBeInTheDocument()
    expect(screen.getByText(/cx90-compressor × 1/)).toBeInTheDocument()
    expect(screen.getByText("Is the vendor contract renewed?")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /Approve plan/ }))
    const approve = screen.getByRole("button", { name: "Approve" })
    expect(approve).toBeDisabled()
    await userEvent.click(screen.getByLabelText(/I confirm the estimated cost/))
    await userEvent.click(approve)

    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].decision).toBe(PlanDecision.APPROVE)
    expect(decisions[0].confirmHighCost).toBe(true)
  })

  it("can't approve a safety-blocked plan", async () => {
    renderPanel(["safety_block"], { safetyBlock: true })

    expect(await screen.findByText("Work is blocked for safety")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Approve plan/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: /Ask for a revision/ })).toBeEnabled()
  })

  it("shows nothing to people without incidents:read", () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <TransportProvider transport={fakeBackend("tenant")}>
          <IdentityProvider initialDevUser="tenant">
            <SessionProvider>
            <IncidentPanel requestId="request-1" />
          </SessionProvider>
          </IdentityProvider>
        </TransportProvider>
      </QueryClientProvider>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe("Tenant notices", () => {
  it("lets the manager approve the communicator's draft, saying it will be translated", async () => {
    const sent: { subject: string; body: string }[] = []
    const approved = { ...incident, status: IncidentStatus.APPROVED }
    const transport = fakeBackend("facility_manager", (router) =>
      router.service(IncidentService, {
        listIncidents: () => ({ incidents: [approved], total: 1 }),
        getIncident: () => ({
          incident: approved,
          plans: [
            create(PlanVersionSchema, {
              id: "plan-1",
              version: 1,
              status: "approved",
              summary: "Restore the boiler.",
              tenantNotice: { subject: "Heating update", body: "An engineer is coming today." },
            }),
          ],
          actions: [],
          notices: [],
          tenantLanguage: "fr",
        }),
        sendTenantNotice: (request) => {
          sent.push({ subject: request.subject, body: request.body })
          return { notice: { id: "n1", subject: request.subject, body: request.body, language: "fr" } }
        },
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TransportProvider transport={transport}>
          <IdentityProvider initialDevUser="facility_manager">
            <SessionProvider>
              <IncidentPanel requestId="request-1" />
            </SessionProvider>
          </IdentityProvider>
        </TransportProvider>
      </QueryClientProvider>,
    )

    expect(await screen.findByDisplayValue("Heating update")).toBeInTheDocument()
    expect(screen.getByText(/machine-translated into French/)).toBeInTheDocument()
    const message = screen.getByLabelText("Message")
    await userEvent.type(message, " Dear [[PII_0001]")
    expect(screen.getByRole("button", { name: "Approve and send" })).toBeDisabled()
    await userEvent.clear(message)
    await userEvent.type(message, "An engineer is coming this afternoon.")
    await userEvent.click(screen.getByRole("button", { name: "Approve and send" }))
    await waitFor(() => expect(sent).toEqual([{ subject: "Heating update", body: "An engineer is coming this afternoon." }]))
  })

  it("shows the tenant their language first, with the approved English one click away", async () => {
    render(
      <NoticesCard
        notices={[
          create(TenantNoticeSchema, {
            id: "n1",
            subject: "Heating update",
            body: "An engineer is coming today.",
            language: "fr",
            translatedSubject: "Mise à jour du chauffage",
            translatedBody: "Un technicien passe aujourd'hui.",
          }),
        ]}
      />,
    )
    expect(screen.getByText("Un technicien passe aujourd'hui.")).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole("button", { name: "Show original" })[1])
    expect(screen.getByText("An engineer is coming today.")).toBeInTheDocument()
  })
})
