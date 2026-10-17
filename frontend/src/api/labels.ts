/**
 * Every enum and reason code shown to people is worded here, once (frontend spec section 8).
 * Unknown values fall back to a humanized code, so a new backend value never breaks the UI.
 */
import { MaintenanceCategory, IntakeStatus, RequestSource } from "@/gen/facilityops/v1/intake_pb"
import {
  DismissalReason,
  RejectionReason,
  ReviewCaseStatus,
  WorkOrderStatus,
} from "@/gen/facilityops/v1/operations_pb"
import { EvaluationMode, EvaluationStatus } from "@/gen/facilityops/v1/evaluations_pb"
import { UserStatus } from "@/gen/facilityops/v1/users_pb"

export type Tone = "neutral" | "info" | "success" | "warning" | "danger"

export function humanize(code: string): string {
  const text = code.replace(/[_:.]+/g, " ").trim()
  return text ? text[0].toUpperCase() + text.slice(1) : ""
}

export const categoryLabels: Record<MaintenanceCategory, string> = {
  [MaintenanceCategory.UNSPECIFIED]: "Not classified",
  [MaintenanceCategory.HVAC]: "Heating & cooling",
  [MaintenanceCategory.PLUMBING]: "Plumbing",
  [MaintenanceCategory.ELECTRICAL]: "Electrical",
  [MaintenanceCategory.SECURITY]: "Security & safety",
  [MaintenanceCategory.STRUCTURAL]: "Structural",
  [MaintenanceCategory.CLEANING]: "Cleaning",
  [MaintenanceCategory.OTHER]: "Other",
}

export const selectableCategories = Object.values(MaintenanceCategory).filter(
  (value): value is MaintenanceCategory =>
    typeof value === "number" && value !== MaintenanceCategory.UNSPECIFIED,
)

export const sourceLabels: Record<RequestSource, string> = {
  [RequestSource.UNSPECIFIED]: "Text",
  [RequestSource.TEXT]: "Text",
  [RequestSource.PHOTO]: "Photo",
  [RequestSource.VOICE]: "Voice note",
  [RequestSource.DOCUMENT]: "Document",
}

/** Intake status, worded differently for the tenant who reported it and for staff. */
export function intakeStatusLabel(status: IntakeStatus, audience: "tenant" | "staff"): { label: string; tone: Tone } {
  const tenant = audience === "tenant"
  switch (status) {
    case IntakeStatus.EMERGENCY_HANDOFF:
      return { label: tenant ? "Sent to on-call team" : "Emergency handoff", tone: "danger" }
    case IntakeStatus.HUMAN_REVIEW:
      return { label: tenant ? "Being reviewed" : "Human review", tone: "warning" }
    case IntakeStatus.PENDING_APPROVAL:
      return { label: tenant ? "Awaiting approval" : "Pending approval", tone: "info" }
    case IntakeStatus.WORK_ORDER_CREATED:
      return { label: tenant ? "Work order created" : "Work order", tone: "success" }
    case IntakeStatus.DISMISSED:
      return { label: tenant ? "Closed" : "Dismissed", tone: "neutral" }
    default:
      return { label: "Processing", tone: "neutral" }
  }
}

export const workOrderStatusLabels: Record<WorkOrderStatus, { label: string; tone: Tone }> = {
  [WorkOrderStatus.UNSPECIFIED]: { label: "Unknown", tone: "neutral" },
  [WorkOrderStatus.PENDING_APPROVAL]: { label: "Pending approval", tone: "info" },
  [WorkOrderStatus.OPEN]: { label: "Open", tone: "warning" },
  [WorkOrderStatus.REJECTED]: { label: "Rejected", tone: "neutral" },
  [WorkOrderStatus.COMPLETED]: { label: "Completed", tone: "success" },
}

/** The backend reports work-order status on requests as a plain name, e.g. "open". */
export function workOrderStatusFromName(name: string): { label: string; tone: Tone } | null {
  if (!name) return null
  const key = name.toUpperCase() as keyof typeof WorkOrderStatus
  const value = WorkOrderStatus[key]
  return value === undefined ? { label: humanize(name), tone: "neutral" } : workOrderStatusLabels[value]
}

export const reviewCaseStatusLabels: Record<ReviewCaseStatus, { label: string; tone: Tone }> = {
  [ReviewCaseStatus.UNSPECIFIED]: { label: "Unknown", tone: "neutral" },
  [ReviewCaseStatus.QUEUED]: { label: "Queued", tone: "warning" },
  [ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE]: { label: "Emergency", tone: "danger" },
  [ReviewCaseStatus.RESOLVED]: { label: "Resolved", tone: "success" },
}

export const dismissalReasonLabels: Record<DismissalReason, string> = {
  [DismissalReason.UNSPECIFIED]: "",
  [DismissalReason.DUPLICATE]: "Duplicate of another request",
  [DismissalReason.NOT_ACTIONABLE]: "Not actionable",
  [DismissalReason.OUT_OF_SCOPE]: "Out of scope",
  [DismissalReason.HANDLED_BY_ON_CALL]: "Handled by the on-call team",
  [DismissalReason.RESOLVED_BY_TENANT]: "Resolved by the tenant",
  [DismissalReason.ABUSIVE_OR_SPAM]: "Abusive or spam",
}

export const rejectionReasonLabels: Record<RejectionReason, string> = {
  [RejectionReason.UNSPECIFIED]: "",
  [RejectionReason.DUPLICATE]: "Duplicate",
  [RejectionReason.NOT_ACTIONABLE]: "Not actionable",
  [RejectionReason.COST_NOT_APPROVED]: "Cost not approved",
  [RejectionReason.SAFETY_REVIEW_NEEDED]: "Needs a safety review first",
  [RejectionReason.OUT_OF_SCOPE]: "Out of scope",
}

/** Why the workflow sent a request to a person, in plain words for staff. */
const reasonCodeLabels: Record<string, string> = {
  classification_uncertain: "The AI wasn't confident",
  classification_unconfirmed: "The AI wasn't consistent",
  classification_unavailable: "Classifier unavailable",
  classified: "Classified automatically",
  classified_by_agreement: "Classified (confirmed by agreement)",
  approval_required: "Needs approval (priority 4 or higher)",
  pii_redaction_unavailable: "AI services unavailable",
  pii_redaction_empty_result: "Nothing left after redaction",
  content_safety_unavailable: "Content Safety unavailable",
  prompt_attack_detected: "Blocked by Prompt Shields",
  document_attack_detected: "Hidden instructions found in the attachment",
  image_safety_unavailable: "Image safety check unavailable",
  media_transcription_unavailable: "Photo, document or voice couldn't be processed",
  media_unreadable: "Nothing readable in the attachment",
  media_type_mismatch: "File isn't the type it claims to be",
  media_corrupt: "File is damaged",
  image_too_small: "Image too small to see anything",
  image_too_large: "Image too large",
  document_encrypted: "Document is password-protected",
  document_too_many_pages: "Document has too many pages",
  work_order_creation_failed: "Work order couldn't be created",
  emergency_gas: "Emergency: gas",
  emergency_carbon_monoxide: "Emergency: carbon monoxide",
  emergency_fire: "Emergency: fire or smoke",
  emergency_electrical: "Emergency: electrical hazard",
  emergency_flood: "Emergency: flooding",
}

export function reasonLabel(code: string): string {
  if (code.startsWith("harmful_content_")) return `Blocked by Content Safety (${humanize(code.slice(16))})`
  if (code.startsWith("harmful_image_")) return `Image flagged by Content Safety (${humanize(code.slice(14))})`
  return reasonCodeLabels[code] ?? humanize(code)
}

export const priorityLabels: Record<number, { label: string; tone: Tone }> = {
  1: { label: "Low", tone: "neutral" },
  2: { label: "Normal", tone: "neutral" },
  3: { label: "Medium", tone: "info" },
  4: { label: "High", tone: "warning" },
  5: { label: "Urgent", tone: "danger" },
}

export const evaluationStatusLabels: Record<EvaluationStatus, { label: string; tone: Tone }> = {
  [EvaluationStatus.UNSPECIFIED]: { label: "Unknown", tone: "neutral" },
  [EvaluationStatus.RUNNING]: { label: "Running", tone: "info" },
  [EvaluationStatus.PASSED]: { label: "Passed", tone: "success" },
  [EvaluationStatus.FAILED]: { label: "Gate failed", tone: "danger" },
  [EvaluationStatus.ERROR]: { label: "Error", tone: "danger" },
}

export const evaluationModeLabels: Record<EvaluationMode, string> = {
  [EvaluationMode.UNSPECIFIED]: "",
  [EvaluationMode.OFFLINE]: "Offline",
  [EvaluationMode.LIVE]: "Live",
}

export const userStatusLabels: Record<UserStatus, { label: string; tone: Tone }> = {
  [UserStatus.UNSPECIFIED]: { label: "Unknown", tone: "neutral" },
  [UserStatus.ACTIVE]: { label: "Active", tone: "success" },
  [UserStatus.DISABLED]: { label: "Disabled", tone: "neutral" },
}

export const roleLabels: Record<string, string> = {
  tenant: "Tenant",
  technician: "Technician",
  facility_manager: "Facility manager",
  admin: "Administrator",
}

export function roleLabel(role: string): string {
  return roleLabels[role] ?? humanize(role)
}

/** Readable names for evaluation metrics, used in tables and the trend picker. */
export const metricLabels: Record<string, string> = {
  emergency_recall: "Emergency recall",
  emergency_precision: "Emergency precision",
  category_accuracy: "Category accuracy",
  priority_exact: "Priority (exact)",
  priority_within_1: "Priority (within 1)",
  ambiguous_to_human_review: "Ambiguous → review",
  injection_resistance: "Injection resistance",
  counterfactual_consistency: "Fairness (single sample)",
  counterfactual_route_consistency_modal: "Route fairness",
  counterfactual_consistency_modal: "Label fairness (modal)",
  route_self_agreement: "Route self-agreement",
  classification_self_agreement: "Label self-agreement",
  automation_rate: "Automation rate",
  automated_category_accuracy: "Automated accuracy",
  classifier_calls_per_request: "Calls per request",
  classifier_error_rate: "Classifier errors",
}

export function metricLabel(name: string): string {
  return metricLabels[name] ?? humanize(name)
}
