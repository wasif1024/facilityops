import { createRouterTransport, type ConnectRouter } from "@connectrpc/connect"

import { AccountService } from "@/gen/facilityops/v1/account_pb"

/** Permissions per role, as seeded by the backend (backend/app/domain/rbac.py). */
export const rolePermissions: Record<string, string[]> = {
  tenant: ["intake:submit", "intake:read_own"],
  technician: ["intake:submit", "work_orders:read_assigned", "work_orders:update_assigned", "pii:reveal_assigned"],
  facility_manager: [
    "intake:submit",
    "intake:read_all",
    "work_orders:read_all",
    "work_orders:approve",
    "work_orders:assign",
    "review_queue:manage",
    "pii:reveal_all",
    "evaluations:read",
    "monitoring:read",
    "decisions:read",
    "incidents:read",
    "incidents:manage",
  ],
  admin: ["users:read", "users:manage", "roles:read", "audit:read", "evaluations:read", "evaluations:run", "monitoring:read", "decisions:read"],
}

/**
 * An in-memory backend: Connect's router transport runs these handlers in the test,
 * so components exercise their real hooks and generated clients without a network.
 */
export function fakeBackend(role: keyof typeof rolePermissions, routes?: (router: ConnectRouter) => void) {
  return createRouterTransport((router) => {
    router.service(AccountService, {
      getMe: () => ({
        userId: `${role}-id`,
        displayName: role,
        roles: [role],
        permissions: rolePermissions[role],
      }),
    })
    routes?.(router)
  })
}
