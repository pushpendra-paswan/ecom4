// Formatting used by several views: money, dates and status labels.
const rupeeFormatter = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

const TICKET_STATUS_LABELS = {
  open: "Open",
  awaiting_approval: "Waiting for review",
  escalated: "With our support team",
  resolved: "Resolved",
};

// Order statuses and shipment statuses
const ORDER_STATUS_LABELS = {
  placed: "Placed",
  shipped: "Shipped",
  delivered: "Delivered",
  cancelled: "Cancelled",
  in_transit: "In transit",
};

// 4500 -> "₹4,500", 125000.5 -> "₹1,25,000.5"
export function formatMoney(amount) {
  return `₹${rupeeFormatter.format(Number(amount))}`;
}

// A timestamp from the API ("2026-11-15T10:00:00+00:00") in the browser's local time
export function formatDateTime(isoTimestamp) {
  return new Date(isoTimestamp).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// A date-only value ("2026-11-15"); built from its parts so it never shifts a day with the time zone
export function formatDate(isoDate) {
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function ticketStatusLabel(status) {
  return TICKET_STATUS_LABELS[status] || status;
}

export function orderStatusLabel(status) {
  return ORDER_STATUS_LABELS[status] || status;
}
