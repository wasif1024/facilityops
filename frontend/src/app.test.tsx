import { Code, ConnectError, createRouterTransport, type Transport } from "@connectrpc/connect"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it } from "vitest"

import { App } from "@/app"
import { AccountService } from "@/gen/facilityops/v1/account_pb"
import { IntakeService, IntakeStatus, MaintenanceCategory } from "@/gen/facilityops/v1/intake_pb"
import { routes } from "@/routes"
import { fakeBackend } from "@/test/fake-backend"

function renderApp(transport: Transport, path = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(<App transport={transport} router={router} devUser="test-user" />)
  return router
}

describe("development sign-in", () => {
  beforeEach(() => window.localStorage.clear())

  it("starts on the demo account page, with no default identity", async () => {
    const router = createMemoryRouter(routes, { initialEntries: ["/admin/users"] })
    render(<App transport={fakeBackend("tenant")} router={router} />)
    expect(await screen.findByText("Choose a demo account")).toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument()

    // Signing in starts on the account's own page, not on the admin URL that was opened.
    await userEvent.setup().click(screen.getByRole("button", { name: /Tenant/ }))
    expect(await screen.findByRole("heading", { name: "Report a problem" })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/report")
  })

  it("signs in as the chosen account and signs out back to the chooser", async () => {
    const user = userEvent.setup()
    render(<App transport={fakeBackend("tenant")} router={createMemoryRouter(routes, { initialEntries: ["/"] })} />)
    await user.click(await screen.findByRole("button", { name: /Tenant/ }))
    expect(await screen.findByRole("heading", { name: "Report a problem" })).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Sign out/ }))
    expect(await screen.findByText("Choose a demo account")).toBeInTheDocument()
  })
})

describe("routing by permission", () => {
  it("lands a tenant on Report a problem and shows only tenant sections", async () => {
    renderApp(fakeBackend("tenant"))
    expect(await screen.findByRole("heading", { name: "Report a problem" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument()
  })

  it("explains instead of showing a page the role can't use", async () => {
    renderApp(fakeBackend("admin"), "/review")
    expect(await screen.findByText("You don't have access to this page")).toBeInTheDocument()
  })

  it("shows a clear error when the backend is unreachable", async () => {
    const down = createRouterTransport((router) =>
      router.service(AccountService, {
        getMe: () => {
          throw new ConnectError("down", Code.Unavailable)
        },
      }),
    )
    renderApp(down)
    expect(await screen.findByText("Something went wrong on our side", {}, { timeout: 5000 })).toBeInTheDocument()
  })
})

describe("report a problem", () => {
  it("reuses the same requestId when a failed submit is retried", async () => {
    const requestIds: string[] = []
    let attempts = 0
    const transport = fakeBackend("tenant", (router) =>
      router.service(IntakeService, {
        submitIntake: (request) => {
          requestIds.push(request.requestId)
          attempts += 1
          // Not retryable by the query layer, so the user sees the error and retries.
          if (attempts === 1) throw new ConnectError("Rate limit exceeded", Code.ResourceExhausted)
          return {
            result: {
              requestId: request.requestId,
              status: IntakeStatus.WORK_ORDER_CREATED,
              reasonCode: "classified",
              category: MaintenanceCategory.PLUMBING,
              priority: 2,
            },
          }
        },
      }),
    )
    const user = userEvent.setup()
    renderApp(transport, "/report")

    await user.type(await screen.findByLabelText("What's the problem?"), "The kitchen tap drips.")
    await user.click(screen.getByRole("button", { name: "Send report" }))
    expect(await screen.findByText("Too many requests")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Send/ }))
    expect(await screen.findByText(/a work order is on its way/)).toBeInTheDocument()
    expect(requestIds).toHaveLength(2)
    expect(requestIds[0]).toBe(requestIds[1])
  })

  it("shows the emergency panel for an emergency handoff", async () => {
    const transport = fakeBackend("tenant", (router) =>
      router.service(IntakeService, {
        submitIntake: (request) => ({
          result: { requestId: request.requestId, status: IntakeStatus.EMERGENCY_HANDOFF, reasonCode: "emergency_gas" },
        }),
      }),
    )
    const user = userEvent.setup()
    renderApp(transport, "/report")

    await user.type(await screen.findByLabelText("What's the problem?"), "Strong gas smell")
    await user.click(screen.getByRole("button", { name: "Send report" }))

    expect(await screen.findByText("We've alerted the on-call team")).toBeInTheDocument()
    // The next step is hidden until the tenant acknowledges the safety advice.
    expect(screen.queryByRole("button", { name: "Report another problem" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "I understand" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Report another problem" })).toBeInTheDocument())
  })
})
