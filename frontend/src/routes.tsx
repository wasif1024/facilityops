import { lazy, type ReactNode, Suspense } from "react"
import { Navigate, type RouteObject, createBrowserRouter } from "react-router"

import type { PermissionId } from "@/auth/permissions"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { AppShell } from "@/components/app/app-shell"
import { ListSkeleton, NoAccess, NotFound } from "@/components/app/states"
import { UsersPage } from "@/features/admin/users-page"
import { AuditPage } from "@/features/admin/audit-page"
import { ReportPage } from "@/features/report/report-page"
import { AllRequestsPage, MyRequestsPage, RequestDetailPage } from "@/features/requests/requests-pages"
import { ReviewQueuePage } from "@/features/review/review-queue-page"
import { WorkOrderDetailPage, WorkOrdersPage } from "@/features/work-orders/work-orders-pages"
import { landingRoute } from "@/navigation"

// Charts are only needed on the Insights pages, so they load on demand (spec 9.6).
const MonitoringPage = lazy(() =>
  import("@/features/insights/monitoring-page").then((m) => ({ default: m.MonitoringPage })),
)
const EvaluationsPage = lazy(() =>
  import("@/features/insights/evaluations-pages").then((m) => ({ default: m.EvaluationsPage })),
)
const EvaluationDetailPage = lazy(() =>
  import("@/features/insights/evaluations-pages").then((m) => ({ default: m.EvaluationDetailPage })),
)

/** Renders the page only if the session holds one of the permissions; otherwise explains why not. */
function Guard({ anyOf, children }: { anyOf: PermissionId[]; children: ReactNode }) {
  const { canAny } = useSession()
  if (!canAny(...anyOf)) return <NoAccess />
  return <Suspense fallback={<ListSkeleton />}>{children}</Suspense>
}

function Home() {
  const { canAny } = useSession()
  return <Navigate to={landingRoute(canAny)} replace />
}

const page = (anyOf: PermissionId[], element: ReactNode) => <Guard anyOf={anyOf}>{element}</Guard>

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Home /> },
      { path: "report", element: page([Permission.IntakeSubmit], <ReportPage />) },
      { path: "requests/mine", element: page([Permission.IntakeReadOwn], <MyRequestsPage />) },
      { path: "requests", element: page([Permission.IntakeReadAll], <AllRequestsPage />) },
      {
        path: "requests/:requestId",
        element: page([Permission.IntakeReadOwn, Permission.IntakeReadAll], <RequestDetailPage />),
      },
      { path: "review", element: page([Permission.ReviewQueueManage], <ReviewQueuePage />) },
      {
        path: "work-orders",
        element: page([Permission.WorkOrdersReadAll, Permission.WorkOrdersReadAssigned], <WorkOrdersPage />),
      },
      {
        path: "work-orders/:workOrderId",
        element: page([Permission.WorkOrdersReadAll, Permission.WorkOrdersReadAssigned], <WorkOrderDetailPage />),
      },
      { path: "monitoring", element: page([Permission.MonitoringRead], <MonitoringPage />) },
      { path: "evaluations", element: page([Permission.EvaluationsRead], <EvaluationsPage />) },
      { path: "evaluations/:runId", element: page([Permission.EvaluationsRead], <EvaluationDetailPage />) },
      { path: "admin/users", element: page([Permission.UsersRead], <UsersPage />) },
      { path: "admin/audit", element: page([Permission.AuditRead], <AuditPage />) },
      { path: "no-access", element: <NoAccess /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
