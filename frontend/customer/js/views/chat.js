// One ticket's conversation: messages, status, takeover banner, typing indicator and the message box.
import { apiRequest } from "../api.js";
import { formatDateTime, ticketStatusLabel } from "../format.js";

const TYPING_TIMEOUT_MS = 60000;
const SUPPORT_HOURS_TEXT =
  "Our support team will reply during support hours, Monday to Saturday, 9:00 to 19:00 IST.";
const SENDER_LABELS = { customer: "You", ai: "AI assistant", agent: "Support agent" };

// Returns the refresh function main.js calls on realtime events (or with null after a reconnect)
export function renderChat(container, ticketId) {
  // Step 1: the page skeleton; it is built once and only its parts are updated later
  const backLink = document.createElement("a");
  backLink.href = "#/tickets";
  backLink.className = "back-link";
  backLink.textContent = "← All tickets";

  const titleRow = document.createElement("div");
  titleRow.className = "title-row";
  const title = document.createElement("h1");
  title.textContent = "Loading conversation…";
  const statusBadge = document.createElement("span");
  statusBadge.className = "badge";
  statusBadge.hidden = true;
  titleRow.append(title, statusBadge);

  const banner = document.createElement("p");
  banner.className = "banner";
  banner.textContent = "A support agent is handling this conversation";
  banner.hidden = true;

  const refreshError = document.createElement("p");
  refreshError.className = "error";
  refreshError.setAttribute("role", "alert");
  refreshError.hidden = true;

  const messageList = document.createElement("ol");
  messageList.className = "message-list";
  messageList.setAttribute("aria-live", "polite");
  messageList.setAttribute("aria-label", "Conversation");
  messageList.hidden = true;

  const typingIndicator = document.createElement("p");
  typingIndicator.className = "typing";
  typingIndicator.textContent = "AI assistant is typing…";
  typingIndicator.hidden = true;

  const hoursNote = document.createElement("p");
  hoursNote.className = "muted small";
  hoursNote.textContent = SUPPORT_HOURS_TEXT;
  hoursNote.hidden = true;

  // Step 2: the message box (Enter sends, Shift+Enter adds a new line)
  const form = document.createElement("form");
  form.className = "message-form";
  form.hidden = true;
  const messageLabel = document.createElement("label");
  messageLabel.className = "visually-hidden";
  messageLabel.htmlFor = "message-input";
  messageLabel.textContent = "Your message";
  const messageInput = document.createElement("textarea");
  messageInput.id = "message-input";
  messageInput.rows = 2;
  messageInput.maxLength = 5000;
  messageInput.placeholder = "Write a message…";
  const sendButton = document.createElement("button");
  sendButton.type = "submit";
  sendButton.className = "primary";
  sendButton.textContent = "Send";
  const sendError = document.createElement("p");
  sendError.className = "error";
  sendError.setAttribute("role", "alert");
  sendError.hidden = true;
  form.append(messageLabel, messageInput, sendButton, sendError);

  container.append(backLink, titleRow, banner, refreshError, messageList, typingIndicator, hoursNote, form);

  // Step 3: load the ticket and show what changed (first time, on events for this ticket, after a reconnect)
  const shownMessageIds = new Set();
  let typingTimer = null;
  let latestLoad = 0;
  const loadConversation = async () => {
    const thisLoad = ++latestLoad;
    let result;
    try {
      result = await apiRequest("GET", `/api/tickets/tickets/${encodeURIComponent(ticketId)}`);
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      // Step 3a: a ticket that is not ours (404) or a malformed id (422) is simply "not found"
      if (shownMessageIds.size === 0) {
        title.textContent =
          error.status === 404 || error.status === 422 ? "Ticket not found" : "Could not load this ticket";
        refreshError.textContent =
          error.status === 404 || error.status === 422
            ? "This ticket does not exist or does not belong to your account."
            : error.message;
      } else {
        refreshError.textContent = `Could not refresh the conversation: ${error.message}`;
      }
      refreshError.hidden = false;
      return;
    }
    if (thisLoad !== latestLoad) {
      return; // a newer load is already on its way
    }
    const ticket = result.ticket;
    const messages = result.messages;
    refreshError.hidden = true;

    // Step 3b: subject, status and the takeover banner
    title.textContent = ticket.subject;
    statusBadge.textContent = ticketStatusLabel(ticket.status);
    statusBadge.className = `badge status-${ticket.status}`;
    statusBadge.hidden = false;
    banner.hidden = !ticket.taken_over_by;
    messageList.hidden = false;
    form.hidden = false;

    // Step 3c: add only the messages we have not shown yet (messages never change once posted)
    let addedCount = 0;
    for (const message of messages) {
      if (shownMessageIds.has(message.id)) {
        continue;
      }
      shownMessageIds.add(message.id);
      addedCount = addedCount + 1;

      const item = document.createElement("li");
      item.className = `message from-${message.sender_type}`;
      if (message.sender_type !== "system") {
        const sender = document.createElement("span");
        sender.className = "sender";
        sender.textContent = SENDER_LABELS[message.sender_type] || message.sender_type;
        item.append(sender);
      }
      const text = document.createElement("p");
      text.className = "message-text";
      text.textContent = message.content;
      const time = document.createElement("time");
      time.className = "muted small";
      time.dateTime = message.created_at;
      time.textContent = formatDateTime(message.created_at);
      item.append(text, time);
      messageList.append(item);
    }
    if (addedCount > 0) {
      messageList.scrollTop = messageList.scrollHeight;
    }

    // Step 3d: typing indicator, simulated on the client: the newest message is the customer's,
    // less than 60 seconds old, and the AI is expected to answer (not taken over, escalated or awaiting review)
    clearTimeout(typingTimer);
    const lastMessage = messages[messages.length - 1];
    const lastMessageAge = lastMessage ? Date.now() - Date.parse(lastMessage.created_at) : TYPING_TIMEOUT_MS;
    const aiShouldAnswer = !ticket.taken_over_by && ticket.status !== "escalated" && ticket.status !== "awaiting_approval";
    const waitingForReply = lastMessage && lastMessage.sender_type === "customer" && lastMessageAge < TYPING_TIMEOUT_MS;
    typingIndicator.hidden = !(aiShouldAnswer && waitingForReply);
    if (!typingIndicator.hidden) {
      const remaining = Math.min(TYPING_TIMEOUT_MS - lastMessageAge, TYPING_TIMEOUT_MS);
      typingTimer = setTimeout(() => {
        typingIndicator.hidden = true;
      }, remaining);
    }

    // Step 3e: an escalated ticket waits for a human (the banner already explains a takeover)
    hoursNote.hidden = !(ticket.status === "escalated" && !ticket.taken_over_by);
  };
  loadConversation();

  // Step 4: send a message; keep the text if it fails
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const content = messageInput.value.trim();
    if (content === "" || sendButton.disabled) {
      return;
    }
    sendError.hidden = true;
    sendButton.disabled = true;
    messageInput.disabled = true;
    sendButton.textContent = "Sending…";
    try {
      await apiRequest("POST", `/api/tickets/tickets/${encodeURIComponent(ticketId)}/messages`, { content });
      messageInput.value = "";
      await loadConversation();
    } catch (error) {
      sendError.textContent = `Your message was not sent: ${error.message}`;
      sendError.hidden = false;
    } finally {
      sendButton.disabled = false;
      messageInput.disabled = false;
      sendButton.textContent = "Send";
      messageInput.focus();
    }
  });

  // Step 5: Enter sends, Shift+Enter keeps the new line (and never send while an IME is composing)
  messageInput.addEventListener("keydown", (keyEvent) => {
    if (keyEvent.key === "Enter" && !keyEvent.shiftKey && !keyEvent.isComposing) {
      keyEvent.preventDefault();
      form.requestSubmit();
    }
  });

  // Step 6: refetch on events about this ticket, and on every reconnect (event is null)
  return (event) => {
    if (event === null || event.data.ticket_id === ticketId) {
      loadConversation();
    }
  };
}
