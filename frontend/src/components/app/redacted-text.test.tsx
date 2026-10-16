import { TransportProvider } from "@connectrpc/connect-query"
import { act, render, renderHook, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RedactedText, useReveal } from "@/components/app/redacted-text"
import { IntakeService } from "@/gen/facilityops/v1/intake_pb"
import { fakeBackend } from "@/test/fake-backend"

describe("RedactedText", () => {
  it("hides placeholders behind neutral chips", () => {
    render(<RedactedText text="[PII_0001] in unit 3A, call [PII_0002]" />)
    expect(screen.getAllByText("personal detail")).toHaveLength(2)
    expect(screen.queryByText(/PII_/)).not.toBeInTheDocument()
  })

  it("shows revealed values in place", () => {
    render(<RedactedText text="[PII_0001] in unit 3A" revealed={{ "[PII_0001]": "Avery Synthetic" }} />)
    expect(screen.getByText("Avery Synthetic").tagName).toBe("MARK")
  })
})

describe("useReveal", () => {
  afterEach(() => vi.useRealTimers())

  function setup() {
    const calls: string[] = []
    const transport = fakeBackend("facility_manager", (router) =>
      router.service(IntakeService, {
        revealRequestDetails: (request) => {
          calls.push(request.requestId)
          return { requestId: request.requestId, values: { "[PII_0001]": "Avery Synthetic" } }
        },
      }),
    )
    const wrapper = ({ children }: { children: ReactNode }) => (
      <TransportProvider transport={transport}>{children}</TransportProvider>
    )
    return { calls, ...renderHook(() => useReveal("request-1"), { wrapper }) }
  }

  it("keeps revealed values only in state and clears them after two minutes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { result, calls } = setup()

    await act(() => result.current.reveal())
    await waitFor(() => expect(result.current.values).toEqual({ "[PII_0001]": "Avery Synthetic" }))
    expect(calls).toEqual(["request-1"])
    expect(JSON.stringify(localStorage)).not.toContain("Avery")
    expect(JSON.stringify(sessionStorage)).not.toContain("Avery")

    act(() => vi.advanceTimersByTime(2 * 60 * 1000))
    expect(result.current.values).toBeUndefined()
  })

  it("hides on demand", async () => {
    const { result } = setup()
    await act(() => result.current.reveal())
    act(() => result.current.hide())
    expect(result.current.values).toBeUndefined()
  })
})
