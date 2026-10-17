/** Permission ids, as granted by the backend's RBAC seed (backend/app/domain/rbac.py). */
export const Permission = {
  IntakeSubmit: "intake:submit",
  IntakeReadOwn: "intake:read_own",
  IntakeReadAll: "intake:read_all",
  WorkOrdersReadAssigned: "work_orders:read_assigned",
  WorkOrdersReadAll: "work_orders:read_all",
  WorkOrdersApprove: "work_orders:approve",
  WorkOrdersAssign: "work_orders:assign",
  WorkOrdersUpdateAssigned: "work_orders:update_assigned",
  ReviewQueueManage: "review_queue:manage",
  PiiRevealAssigned: "pii:reveal_assigned",
  PiiRevealAll: "pii:reveal_all",
  UsersRead: "users:read",
  UsersManage: "users:manage",
  RolesRead: "roles:read",
  AuditRead: "audit:read",
  EvaluationsRead: "evaluations:read",
  EvaluationsRun: "evaluations:run",
  MonitoringRead: "monitoring:read",
  DecisionsRead: "decisions:read",
} as const

export type PermissionId = (typeof Permission)[keyof typeof Permission]
