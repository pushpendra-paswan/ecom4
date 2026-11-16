// The tickets list and the new-ticket form.
import { apiRequest } from "../api.js";
import { formatDateTime, ticketStatusLabel } from "../format.js";

// Returns the refresh function main.js calls on realtime events (or with null after a reconnect)
export function renderTicketList(container) {
  // Step 1: title row with the "New ticket" button
  const titleRow = document.createElement("div");
  titleRow.className = "title-row";
  const title = document.createElement("h1");
  title.textContent = "Support tickets";
  const newTicketLink = document.createElement("a");
  newTicketLink.className = "button primary";
  newTicketLink.href = "#/new-ticket";
  newTicketLink.textContent = "New ticket";
  titleRow.append(title, newTicketLink);

  // Step 2: the area that shows loading, the list, the empty state or an error
  const listArea = document.createElement("div");
  listArea.textContent = "Loading tickets…";
  container.append(titleRow, listArea);

  // Step 3: load the tickets (first time, on every ticket event, after a reconnect)
  let latestLoad = 0;
  const loadTickets = async () => {
    const thisLoad = ++latestLoad;
    try {
      const tickets = await apiRequest("GET", "/api/tickets/tickets");
      if (thisLoad !== latestLoad) {
        return; // a newer load is already on its way
      }

      // Step 3a: empty state
      if (tickets.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "No tickets yet. Need help? Start a new ticket.";
        listArea.replaceChildren(empty);
        return;
      }

      // Step 3b: one link per ticket, newest updated first (the API already sorts them)
      const list = document.createElement("ul");
      list.className = "item-list";
      for (const ticket of tickets) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.className = "item-link";
        link.href = `#/tickets/${encodeURIComponent(ticket.id)}`;

        const subject = document.createElement("span");
        subject.className = "item-title";
        subject.textContent = ticket.subject;
        const status = document.createElement("span");
        status.className = `badge status-${ticket.status}`;
        status.textContent = ticketStatusLabel(ticket.status);
        const updated = document.createElement("span");
        updated.className = "muted small";
        updated.textContent = `Updated ${formatDateTime(ticket.updated_at)}`;

        link.append(subject, status, updated);
        item.append(link);
        list.append(item);
      }
      listArea.replaceChildren(list);
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent = `Could not load your tickets: ${error.message}`;
      listArea.replaceChildren(errorText);
    }
  };
  loadTickets();

  // Step 4: any ticket event (or a reconnect) can change the list
  return () => loadTickets();
}

// The new-ticket form; orderNumber (from "Get help with this order") prefills the subject
export function renderNewTicket(container, orderNumber) {
  // Step 1: title and back link
  const backLink = document.createElement("a");
  backLink.href = "#/tickets";
  backLink.className = "back-link";
  backLink.textContent = "← All tickets";
  const title = document.createElement("h1");
  title.textContent = "New support ticket";

  // Step 2: the form with labelled subject and message fields
  const form = document.createElement("form");
  form.className = "card stack";

  const subjectLabel = document.createElement("label");
  subjectLabel.textContent = "Subject";
  const subjectInput = document.createElement("input");
  subjectInput.type = "text";
  subjectInput.required = true;
  subjectInput.maxLength = 200;
  if (orderNumber) {
    subjectInput.value = `Help with order ${orderNumber}`;
  }
  subjectLabel.append(subjectInput);

  const messageLabel = document.createElement("label");
  messageLabel.textContent = "How can we help?";
  const messageInput = document.createElement("textarea");
  messageInput.required = true;
  messageInput.maxLength = 5000;
  messageInput.rows = 5;
  messageLabel.append(messageInput);

  const errorText = document.createElement("p");
  errorText.className = "error";
  errorText.setAttribute("role", "alert");
  errorText.hidden = true;

  const submitButton = document.createElement("button");
  submitButton.type = "submit";
  submitButton.className = "primary";
  submitButton.textContent = "Create ticket";

  form.append(subjectLabel, messageLabel, errorText, submitButton);
  container.append(backLink, title, form);

  // Step 3: focus the first empty field
  if (orderNumber) {
    messageInput.focus();
  } else {
    subjectInput.focus();
  }

  // Step 4: create the ticket, then open its conversation
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    errorText.hidden = true;
    submitButton.disabled = true;
    submitButton.textContent = "Creating…";
    try {
      const created = await apiRequest("POST", "/api/tickets/tickets", {
        subject: subjectInput.value.trim(),
        message: messageInput.value.trim(),
      });
      window.location.hash = `#/tickets/${encodeURIComponent(created.ticket.id)}`;
    } catch (error) {
      errorText.textContent = `Could not create the ticket: ${error.message}`;
      errorText.hidden = false;
      submitButton.disabled = false;
      submitButton.textContent = "Create ticket";
    }
  });
}
