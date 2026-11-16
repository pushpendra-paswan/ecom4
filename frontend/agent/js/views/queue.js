// The ticket queue with its filters.
import { apiRequest } from "../api.js";
import { formatDateTime, ticketStatusLabel } from "../format.js";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "awaiting_approval", label: "Waiting for approval" },
  { value: "escalated", label: "Escalated" },
  { value: "resolved", label: "Resolved" },
];
// Tickets with these statuses need a human, so they are highlighted
const NEEDS_HUMAN_STATUSES = ["escalated", "awaiting_approval"];

// The filters stay as they are while the agent moves between tickets (kept in memory only)
let statusFilter = "";
let onlyAssignedToMe = false;
let onlyTakenOver = false;

// Returns { refresh(events), markSelected(ticketId) } for main.js
export function renderQueue(container, currentUser) {
  // Step 1: title and filters (status goes to the API; the two checkboxes filter in the browser)
  const title = document.createElement("h1");
  title.textContent = "Tickets";

  const filters = document.createElement("form");
  filters.className = "filters";
  filters.addEventListener("submit", (submitEvent) => submitEvent.preventDefault());

  const statusLabel = document.createElement("label");
  statusLabel.textContent = "Status";
  const statusSelect = document.createElement("select");
  for (const filter of STATUS_FILTERS) {
    const option = document.createElement("option");
    option.value = filter.value;
    option.textContent = filter.label;
    statusSelect.append(option);
  }
  statusSelect.value = statusFilter;
  statusLabel.append(statusSelect);

  const assignedLabel = document.createElement("label");
  assignedLabel.className = "checkbox";
  const assignedCheckbox = document.createElement("input");
  assignedCheckbox.type = "checkbox";
  assignedCheckbox.checked = onlyAssignedToMe;
  assignedLabel.append(assignedCheckbox, "Assigned to me");

  const takenOverLabel = document.createElement("label");
  takenOverLabel.className = "checkbox";
  const takenOverCheckbox = document.createElement("input");
  takenOverCheckbox.type = "checkbox";
  takenOverCheckbox.checked = onlyTakenOver;
  takenOverLabel.append(takenOverCheckbox, "Taken over");

  filters.append(statusLabel, assignedLabel, takenOverLabel);

  // Step 2: the area that shows loading, the list, the empty state or an error
  const listArea = document.createElement("div");
  listArea.className = "queue-list";
  listArea.textContent = "Loading tickets…";
  container.append(title, filters, listArea);

  // Step 3: load the tickets and draw the list (first time, on filter changes, ticket events, reconnects)
  let selectedTicketId = null;
  let latestLoad = 0;
  const loadTickets = async () => {
    const thisLoad = ++latestLoad;
    let tickets;
    try {
      const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
      tickets = await apiRequest("GET", `/api/tickets/tickets${query}`);
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent = `Could not load the tickets: ${error.message}`;
      listArea.replaceChildren(errorText);
      return;
    }
    if (thisLoad !== latestLoad) {
      return; // a newer load is already on its way
    }

    // Step 3a: the two browser-side filters
    const shownTickets = tickets.filter((ticket) => {
      if (onlyAssignedToMe && ticket.assigned_agent_id !== currentUser.id) {
        return false;
      }
      if (onlyTakenOver && !ticket.taken_over_by) {
        return false;
      }
      return true;
    });

    // Step 3b: empty state
    if (shownTickets.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = tickets.length === 0 ? "No tickets yet." : "No tickets match these filters.";
      listArea.replaceChildren(empty);
      return;
    }

    // Step 3c: one link per ticket, newest updated first (the API already sorts them)
    const list = document.createElement("ul");
    list.className = "item-list";
    for (const ticket of shownTickets) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.className = "item-link queue-item";
      if (NEEDS_HUMAN_STATUSES.includes(ticket.status)) {
        link.classList.add("needs-human");
      }
      link.href = `#/tickets/${encodeURIComponent(ticket.id)}`;
      link.dataset.ticketId = ticket.id;
      if (ticket.id === selectedTicketId) {
        link.setAttribute("aria-current", "true");
      }

      const subject = document.createElement("span");
      subject.className = "item-title";
      subject.textContent = ticket.subject;
      const status = document.createElement("span");
      status.className = `badge status-${ticket.status}`;
      status.textContent = ticketStatusLabel(ticket.status);
      const email = document.createElement("span");
      email.className = "muted small full-row";
      email.textContent = ticket.customer_email;

      const assignment = document.createElement("span");
      assignment.className = "small";
      if (ticket.assigned_agent_id === currentUser.id) {
        assignment.textContent = "You";
      } else if (ticket.assigned_agent_id) {
        assignment.textContent = "Assigned";
      } else {
        assignment.textContent = "Unassigned";
      }
      link.append(subject, status, email, assignment);

      if (ticket.taken_over_by) {
        const takenOver = document.createElement("span");
        takenOver.className = "badge taken-over";
        takenOver.textContent = ticket.taken_over_by === currentUser.id ? "Taken over by you" : "Taken over";
        link.append(takenOver);
      }

      const updated = document.createElement("span");
      updated.className = "muted small";
      updated.textContent = `Updated ${formatDateTime(ticket.updated_at)}`;
      link.append(updated);

      item.append(link);
      list.append(item);
    }
    listArea.replaceChildren(list);
  };
  loadTickets();

  // Step 4: filter changes reload the list
  statusSelect.addEventListener("change", () => {
    statusFilter = statusSelect.value;
    loadTickets();
  });
  assignedCheckbox.addEventListener("change", () => {
    onlyAssignedToMe = assignedCheckbox.checked;
    loadTickets();
  });
  takenOverCheckbox.addEventListener("change", () => {
    onlyTakenOver = takenOverCheckbox.checked;
    loadTickets();
  });

  // Step 5: what main.js calls: refetch on ticket events (or a reconnect), and mark the open ticket
  return {
    refresh: (events) => {
      if (events === null || events.some((event) => event.event_type.startsWith("ticket."))) {
        loadTickets();
      }
    },
    markSelected: (ticketId) => {
      selectedTicketId = ticketId;
      for (const link of listArea.querySelectorAll("[data-ticket-id]")) {
        if (link.dataset.ticketId === ticketId) {
          link.setAttribute("aria-current", "true");
        } else {
          link.removeAttribute("aria-current");
        }
      }
    },
  };
}
