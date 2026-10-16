import type { LucideIcon } from "lucide-react"
import {
  Activity,
  ClipboardCheck,
  FlaskConical,
  Inbox,
  ListChecks,
  MessageSquarePlus,
  ScrollText,
  Users,
  Wrench,
} from "lucide-react"

import { Permission, type PermissionId } from "@/auth/permissions"

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  /** Shown when the session holds any of these permissions (frontend spec section 5). */
  anyOf: PermissionId[]
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const navigation: NavGroup[] = [
  {
    label: "Work",
    items: [
      { label: "Report a problem", to: "/report", icon: MessageSquarePlus, anyOf: [Permission.IntakeSubmit] },
      { label: "My requests", to: "/requests/mine", icon: ListChecks, anyOf: [Permission.IntakeReadOwn] },
      { label: "Review queue", to: "/review", icon: Inbox, anyOf: [Permission.ReviewQueueManage] },
      {
        label: "Work orders",
        to: "/work-orders",
        icon: Wrench,
        anyOf: [Permission.WorkOrdersReadAll, Permission.WorkOrdersReadAssigned],
      },
      { label: "All requests", to: "/requests", icon: ClipboardCheck, anyOf: [Permission.IntakeReadAll] },
    ],
  },
  {
    label: "Insights",
    items: [
      { label: "Monitoring", to: "/monitoring", icon: Activity, anyOf: [Permission.MonitoringRead] },
      { label: "Evaluations", to: "/evaluations", icon: FlaskConical, anyOf: [Permission.EvaluationsRead] },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Users", to: "/admin/users", icon: Users, anyOf: [Permission.UsersRead] },
      { label: "Audit log", to: "/admin/audit", icon: ScrollText, anyOf: [Permission.AuditRead] },
    ],
  },
]

export function visibleNavigation(canAny: (...p: PermissionId[]) => boolean): NavGroup[] {
  return navigation
    .map((group) => ({ ...group, items: group.items.filter((item) => canAny(...item.anyOf)) }))
    .filter((group) => group.items.length > 0)
}

/** Where each role starts (spec 4.2): the first matching screen in this order. */
export function landingRoute(canAny: (...p: PermissionId[]) => boolean): string {
  if (canAny(Permission.ReviewQueueManage)) return "/review"
  if (canAny(Permission.UsersRead)) return "/admin/users"
  if (canAny(Permission.WorkOrdersReadAssigned)) return "/work-orders"
  if (canAny(Permission.IntakeSubmit)) return "/report"
  return "/no-access"
}
