// Refund approvals: filter, context from the Order service, approve and reject.
import { apiRequest } from "../api.js";
import { approvalStatusLabel, formatDateTime, formatMoney } from "../format.js";

const STATUS_FILTERS = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "", label: "All" },
];
const ALREADY_DECIDED_MESSAGE = "This approval was already decided by another staff member.";

// The filter stays as it is while the agent moves around the dashboard (kept in memory only)
let statusFilter = "pending";

// Returns the refresh function main.js calls with a group of events (or null after a reconnect)
export function renderApprovals(container, currentUser) {
  // Step 1: title and the status filter
  const titleRow = document.createElement("div");
  titleRow.className = "title-row";
  const title = document.createElement("h1");
  title.textContent = "Refund approvals";
  const filterLabel = document.createElement("label");
  filterLabel.className = "inline-label";
  filterLabel.textContent = "Show";
  const filterSelect = document.createElement("select");
  for (const filter of STATUS_FILTERS) {
    const option = document.createElement("option");
    option.value = filter.value;
    option.textContent = filter.label;
    filterSelect.append(option);
  }
  filterSelect.value = statusFilter;
  filterLabel.append(filterSelect);
  titleRow.append(title, filterLabel);

  // Step 2: the message line (for example "already decided") and the list area
  const pageMessage = document.createElement("p");
  pageMessage.className = "error";
  pageMessage.setAttribute("role", "alert");
  pageMessage.hidden = true;
  const listArea = document.createElement("div");
  listArea.textContent = "Loading approvals…";
  container.append(titleRow, pageMessage, listArea);

  // Notes typed into the cards, kept when the list is redrawn
  const noteDrafts = new Map();

  // Step 3: load the approvals plus their return requests, then draw the cards
  let lastListJson = "";
  let latestLoad = 0;
  const loadApprovals = async () => {
    const thisLoad = ++latestLoad;
    let approvals;
    let returnRequests;
    try {
      // Step 3a: the approvals (newest first, from the Ticket service)
      const query = statusFilter ? `?status=${statusFilter}` : "";
      approvals = await apiRequest("GET", `/api/tickets/approvals${query}`);

      // Step 3b: the return request behind each pending or approved one (Order service, staff may read any)
      returnRequests = await Promise.all(
        approvals.map(async (approval) => {
          if (approval.status === "rejected") {
            return null;
          }
          try {
            return await apiRequest("GET", `/api/orders/returns/${encodeURIComponent(approval.return_request_id)}`);
          } catch (error) {
            if (error.status === 401) {
              throw error;
            }
            return { loadError: error.status === 404 ? "The return request was not found." : error.message };
          }
        }),
      );
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      lastListJson = "";
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent = `Could not load the approvals: ${error.message}`;
      listArea.replaceChildren(errorText);
      return;
    }
    if (thisLoad !== latestLoad) {
      return; // a newer load is already on its way
    }

    // Step 3c: redraw only when something changed (so a note being typed is not disturbed)
    const listJson = JSON.stringify([approvals, returnRequests]);
    if (listJson === lastListJson) {
      return;
    }
    lastListJson = listJson;

    // Step 3d: empty state
    if (approvals.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = statusFilter === "pending" ? "No approvals are waiting. Nice work." : "No approvals to show.";
      listArea.replaceChildren(empty);
      return;
    }

    // Step 3e: one card per approval
    const list = document.createElement("ul");
    list.className = "approval-list";
    approvals.forEach((approval, index) => {
      const returnRequest = returnRequests[index];
      const card = document.createElement("li");
      card.className = `card approval approval-${approval.status}`;

      // Step 3f: summary, amount and status
      const header = document.createElement("div");
      header.className = "title-row";
      const summary = document.createElement("h2");
      summary.textContent = approval.summary;
      const amount = document.createElement("span");
      amount.className = "amount";
      amount.textContent = formatMoney(approval.amount);
      const status = document.createElement("span");
      status.className = `badge approval-status-${approval.status}`;
      status.textContent = approvalStatusLabel(approval.status);
      header.append(summary, amount, status);
      card.append(header);

      // Step 3g: the facts about the approval
      const facts = document.createElement("dl");
      facts.className = "facts";
      const addFact = (label, value) => {
        const term = document.createElement("dt");
        term.textContent = label;
        const definition = document.createElement("dd");
        definition.append(value);
        facts.append(term, definition);
      };
      addFact("Requested", formatDateTime(approval.created_at));
      const ticketLink = document.createElement("a");
      ticketLink.href = `#/tickets/${encodeURIComponent(approval.ticket_id)}`;
      ticketLink.textContent = "Open ticket";
      addFact("Ticket", ticketLink);
      addFact("Auto-approved", approval.auto_approved ? "Yes, by the refund policy" : "No");

      // Step 3h: the return request (order number, the customer's reason, the refund amount, refund state)
      if (returnRequest && returnRequest.loadError) {
        addFact("Return request", `Could not load it: ${returnRequest.loadError}`);
      } else if (returnRequest) {
        // the return request has no order number; the summary is written by code as "<product> from <order number>"
        const orderNumberMatch = approval.summary.match(/ORD-\d+/);
        if (orderNumberMatch) {
          addFact("Order", orderNumberMatch[0]);
        }
        addFact("Return reason", returnRequest.reason);
        addFact("Return refund amount", formatMoney(returnRequest.refund_amount));
        if (approval.status === "approved") {
          addFact("Refund", returnRequest.status === "refunded" ? "Refund completed" : "Not executed yet");
        }
      }

      // Step 3i: who decided, when, and the note
      if (approval.status !== "pending") {
        let decidedBy = "Another staff member";
        if (approval.auto_approved) {
          decidedBy = "Refund policy (automatic)";
        } else if (approval.decided_by === currentUser.id) {
          decidedBy = "You";
        }
        addFact("Decided by", decidedBy);
        if (approval.decided_at) {
          addFact("Decided", formatDateTime(approval.decided_at));
        }
        if (approval.decision_note) {
          addFact("Note", approval.decision_note);
        }
      }
      card.append(facts);

      // Step 3j: the amount must equal the return's refund amount (the Order service refuses anything else)
      if (returnRequest && !returnRequest.loadError) {
        const approvalCents = Math.round(Number(approval.amount) * 100);
        const returnCents = Math.round(Number(returnRequest.refund_amount) * 100);
        if (approvalCents !== returnCents) {
          const warning = document.createElement("p");
          warning.className = "warning";
          warning.textContent = `Warning: this approval is for ${formatMoney(approval.amount)}, but the return request is for ${formatMoney(returnRequest.refund_amount)}. The Order service will not pay a refund whose amount does not match.`;
          card.append(warning);
        }
      }

      // Step 3k: decision form for pending approvals: approve (optional note) or reject (note required)
      if (approval.status === "pending") {
        const form = document.createElement("form");
        form.className = "decision-form";
        const noteLabel = document.createElement("label");
        noteLabel.textContent = "Note (optional to approve, required to reject; the customer sees a rejection note)";
        const noteInput = document.createElement("textarea");
        noteInput.rows = 2;
        noteInput.maxLength = 1000;
        noteInput.value = noteDrafts.get(approval.id) || "";
        noteLabel.append(noteInput);
        const buttons = document.createElement("div");
        buttons.className = "actions";
        const approveButton = document.createElement("button");
        approveButton.type = "button";
        approveButton.className = "primary";
        approveButton.textContent = "Approve";
        const rejectButton = document.createElement("button");
        rejectButton.type = "button";
        rejectButton.className = "danger";
        rejectButton.textContent = "Reject";
        rejectButton.disabled = noteInput.value.trim() === "";
        const formError = document.createElement("p");
        formError.className = "error";
        formError.setAttribute("role", "alert");
        formError.hidden = true;
        buttons.append(approveButton, rejectButton);
        form.append(noteLabel, buttons, formError);
        card.append(form);

        // Step 3l: remember the note, and enable Reject only when it has text
        noteInput.addEventListener("input", () => {
          noteDrafts.set(approval.id, noteInput.value);
          rejectButton.disabled = noteInput.value.trim() === "";
        });

        // Step 3m: confirm, send the decision, then reload the list
        const decide = async (decision) => {
          const note = noteInput.value.trim();
          const verb = decision === "approve" ? "Approve" : "Reject";
          const confirmed = window.confirm(`${verb} the refund of ${formatMoney(approval.amount)} for ${approval.summary}?`);
          if (!confirmed) {
            return;
          }
          formError.hidden = true;
          approveButton.disabled = true;
          rejectButton.disabled = true;
          const body = decision === "approve" ? (note ? { note } : {}) : { note };
          try {
            await apiRequest("POST", `/api/tickets/approvals/${encodeURIComponent(approval.id)}/${decision}`, body);
            noteDrafts.delete(approval.id);
            pageMessage.hidden = true;
          } catch (error) {
            if (error.status === 409) {
              pageMessage.textContent = ALREADY_DECIDED_MESSAGE;
              pageMessage.hidden = false;
            } else {
              formError.textContent = `The decision was not saved: ${error.message}`;
              formError.hidden = false;
              approveButton.disabled = false;
              rejectButton.disabled = noteInput.value.trim() === "";
              return;
            }
          }
          await loadApprovals();
        };
        approveButton.addEventListener("click", () => decide("approve"));
        rejectButton.addEventListener("click", () => decide("reject"));
      }

      list.append(card);
    });
    listArea.replaceChildren(list);
  };
  loadApprovals();

  // Step 4: changing the filter reloads the list
  filterSelect.addEventListener("change", () => {
    statusFilter = filterSelect.value;
    pageMessage.hidden = true;
    lastListJson = "";
    listArea.textContent = "Loading approvals…";
    loadApprovals();
  });

  // Step 5: approval events and completed refunds change the list (a reconnect refetches it)
  return (events) => {
    const relevant =
      events === null ||
      events.some((event) => event.event_type.startsWith("approval.") || event.event_type === "refund.completed");
    if (relevant) {
      loadApprovals();
    }
  };
}
