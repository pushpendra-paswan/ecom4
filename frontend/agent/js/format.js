// Formatting used by several views: money, dates and labels.
// Copied from the customer app; the labels are the staff versions.
const rupeeFormatter = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

const TICKET_STATUS_LABELS = {
  open: "Open",
  awaiting_approval: "Waiting for approval",
  escalated: "Escalated",
  resolved: "Resolved",
};

const APPROVAL_STATUS_LABELS = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

// The agent's tool tiers (TOOL_TIERS in the agent service)
const TIER_LABELS = {
  1: "Read-only",
  2: "Workflow",
  3: "Approval request",
};

// 4500 -> "₹4,500", 125000.5 -> "₹1,25,000.5"
export function formatMoney(amount) {
  return `₹${rupeeFormatter.format(Number(amount))}`;
}

// A timestamp from the API ("2026-11-15T10:00:00+00:00") in the browser's local time
export function formatDateTime(isoTimestamp) {
  return new Date(isoTimestamp).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ticketStatusLabel(status) {
  return TICKET_STATUS_LABELS[status] || status;
}

export function approvalStatusLabel(status) {
  return APPROVAL_STATUS_LABELS[status] || status;
}

export function tierLabel(tier) {
  return TIER_LABELS[tier] || `Tier ${tier}`;
}
