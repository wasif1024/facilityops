import { Code, ConnectError } from "@connectrpc/connect"
import { describe, expect, it } from "vitest"

import { describeError } from "@/api/errors"
import { humanize, reasonLabel, workOrderStatusFromName } from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { landingRoute, visibleNavigation } from "@/navigation"
import { rolePermissions } from "@/test/fake-backend"

describe("describeError", () => {
  it("shows the backend's message for business-rule errors", () => {
    const error = new ConnectError("At least one active administrator must remain", Code.FailedPrecondition)
    expect(describeError(error)).toMatchObject({
      message: "At least one active administrator must remain",
      retryable: false,
    })
  })

  it("never shows internal details and offers a retry", () => {
    const error = new ConnectError("psycopg: INSERT ... 'Avery Synthetic'", Code.Internal)
    const description = describeError(error)
    expect(description.message).not.toContain("Avery")
    expect(description.retryable).toBe(true)
  })

  it("treats network failures as retryable", () => {
    expect(describeError(new TypeError("Failed to fetch")).retryable).toBe(true)
  })

  it("explains rate limiting", () => {
    expect(describeError(new ConnectError("Rate limit exceeded", Code.ResourceExhausted)).title).toBe("Too many requests")
  })
})

describe("labels", () => {
  it("words known reason codes and humanizes unknown ones", () => {
    expect(reasonLabel("classification_unconfirmed")).toBe("The AI wasn't consistent")
    expect(reasonLabel("harmful_content_violence")).toBe("Blocked by Content Safety (Violence)")
    expect(reasonLabel("brand_new_backend_code")).toBe("Brand new backend code")
  })

  it("maps work-order status names and tolerates unknown ones", () => {
    expect(workOrderStatusFromName("open")?.label).toBe("Open")
    expect(workOrderStatusFromName("")).toBeNull()
    expect(workOrderStatusFromName("on_hold")?.label).toBe("On hold")
    expect(humanize("user.roles_changed")).toBe("User roles changed")
  })
})

describe("navigation", () => {
  const canAnyFor = (role: string) => (...required: string[]) =>
    required.some((p) => rolePermissions[role].includes(p))

  it.each([
    ["tenant", "/report"],
    ["technician", "/work-orders"],
    ["facility_manager", "/review"],
    ["admin", "/admin/users"],
  ])("lands a %s on %s", (role, route) => {
    expect(landingRoute(canAnyFor(role))).toBe(route)
  })

  it("shows each role only its sections", () => {
    const sections = (role: string) => visibleNavigation(canAnyFor(role)).flatMap((g) => g.items.map((i) => i.label))
    expect(sections("tenant")).toEqual(["Report a problem", "My requests"])
    expect(sections("admin")).toEqual(["Monitoring", "Evaluations", "Users", "Audit log"])
    expect(sections("facility_manager")).not.toContain("Users")
  })

  it("covers every permission id the backend can grant", () => {
    const granted = new Set(Object.values(rolePermissions).flat())
    expect(new Set(Object.values(Permission))).toEqual(granted)
  })
})
