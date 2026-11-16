// One ticket for staff: header, actions, the conversation (with a reply box) and the AI activity log.
import { apiRequest } from "../api.js";
import { formatDateTime, formatMoney, ticketStatusLabel, tierLabel } from "../format.js";

// The agent saves a run's activity just after posting the reply, so look again a little later
const DELAYED_ACTIVITY_REFRESH_MS = 3000;
const STATUS_CHOICES = [
  { value: "open", label: "Open" },
  { value: "escalated", label: "Escalated" },
  { value: "resolved", label: "Resolved" },
];
const RUN_TYPE_LABELS = { message: "Customer message", approval: "Approval decision" };
const RUN_STATUS_LABELS = { started: "Running", completed: "Completed", skipped: "Skipped", failed: "Failed" };
// Skip reasons and notes written by the agent service, with a short explanation for staff
const NOTE_EXPLANATIONS = {
  already_answered: "An earlier run had already answered this message.",
  human_replied: "A support agent had already replied, so the AI stayed silent.",
  ticket_escalated: "The ticket is escalated, so the AI stays silent until it is set back to Open.",
  human_takeover: "A support agent has taken over this ticket, so the AI did not reply.",
  awaiting_approval: "A refund is waiting for approval, so the AI only sent its fixed \"under review\" message.",
  reply_discarded_takeover: "The ticket was taken over while the AI was working, so its reply was not posted.",
  message_not_found: "The customer message was not found on the ticket.",
  no_pending_interrupt: "No conversation was paused for this decision, so nothing was announced.",
  lock_busy: "Another run kept this ticket busy for too long; the customer got the fallback message.",
  step_limit: "The AI reached its step limit; the customer got the fallback message.",
};

// Returns the refresh function main.js calls with a group of events (or null after a reconnect)
export function renderTicket(container, ticketId, currentUser) {
  const ticketPath = `/api/tickets/tickets/${encodeURIComponent(ticketId)}`;

  // Step 1: header: title, status, a link to a pending approval, and the facts
  const titleRow = document.createElement("div");
  titleRow.className = "title-row";
  const title = document.createElement("h1");
  title.textContent = "Loading ticket…";
  const statusBadge = document.createElement("span");
  statusBadge.className = "badge";
  statusBadge.hidden = true;
  titleRow.append(title, statusBadge);

  const approvalLink = document.createElement("a");
  approvalLink.className = "notice approval-link";
  approvalLink.href = "#/approvals";
  approvalLink.hidden = true;

  const loadError = document.createElement("p");
  loadError.className = "error";
  loadError.setAttribute("role", "alert");
  loadError.hidden = true;

  const facts = document.createElement("dl");
  facts.className = "facts card";
  facts.hidden = true;
  const factValues = {};
  for (const [key, label] of [
    ["customer", "Customer"],
    ["assignment", "Assigned"],
    ["takeover", "Takeover"],
    ["created", "Created"],
    ["updated", "Updated"],
  ]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const value = document.createElement("dd");
    facts.append(term, value);
    factValues[key] = value;
  }

  // Step 2: the action buttons (each one is shown only when it makes sense, see loadTicket)
  const actions = document.createElement("div");
  actions.className = "actions";
  actions.hidden = true;
  const assignButton = document.createElement("button");
  assignButton.type = "button";
  assignButton.textContent = "Assign to me";
  const takeOverButton = document.createElement("button");
  takeOverButton.type = "button";
  takeOverButton.className = "primary";
  takeOverButton.textContent = "Take over";
  const releaseButton = document.createElement("button");
  releaseButton.type = "button";
  releaseButton.textContent = "Release to AI";

  const statusForm = document.createElement("form");
  statusForm.className = "status-form";
  const statusLabel = document.createElement("label");
  statusLabel.className = "visually-hidden";
  statusLabel.htmlFor = `status-select-${ticketId}`;
  statusLabel.textContent = "New status";
  const statusSelect = document.createElement("select");
  statusSelect.id = `status-select-${ticketId}`;
  const placeholderOption = document.createElement("option");
  placeholderOption.value = "";
  placeholderOption.textContent = "Change status to…";
  statusSelect.append(placeholderOption);
  for (const choice of STATUS_CHOICES) {
    const option = document.createElement("option");
    option.value = choice.value;
    option.textContent = choice.label;
    statusSelect.append(option);
  }
  const statusButton = document.createElement("button");
  statusButton.type = "submit";
  statusButton.textContent = "Change status";
  statusButton.disabled = true;
  statusForm.append(statusLabel, statusSelect, statusButton);

  const takeoverNote = document.createElement("p");
  takeoverNote.className = "muted small";
  takeoverNote.hidden = true;
  const actionError = document.createElement("p");
  actionError.className = "error";
  actionError.setAttribute("role", "alert");
  actionError.hidden = true;
  actions.append(assignButton, takeOverButton, releaseButton, statusForm, takeoverNote, actionError);

  // Step 3: the two tabs
  const tabList = document.createElement("div");
  tabList.className = "tabs";
  tabList.setAttribute("role", "tablist");
  tabList.hidden = true;
  const conversationTab = document.createElement("button");
  conversationTab.type = "button";
  conversationTab.setAttribute("role", "tab");
  conversationTab.textContent = "Conversation";
  const activityTab = document.createElement("button");
  activityTab.type = "button";
  activityTab.setAttribute("role", "tab");
  activityTab.textContent = "AI activity";
  tabList.append(conversationTab, activityTab);

  // Step 4: the conversation panel: messages, the "AI is active" note and the reply box
  const conversationPanel = document.createElement("div");
  conversationPanel.setAttribute("role", "tabpanel");
  conversationPanel.setAttribute("aria-label", "Conversation");
  conversationPanel.hidden = true;

  const messageList = document.createElement("ol");
  messageList.className = "message-list";
  messageList.setAttribute("aria-live", "polite");
  messageList.setAttribute("aria-label", "Messages");

  const aiActiveNote = document.createElement("p");
  aiActiveNote.className = "banner";
  aiActiveNote.textContent = "The AI is active on this ticket. Take over to stop it from replying.";
  aiActiveNote.hidden = true;

  const replyForm = document.createElement("form");
  replyForm.className = "message-form";
  const replyLabel = document.createElement("label");
  replyLabel.className = "visually-hidden";
  replyLabel.htmlFor = `reply-input-${ticketId}`;
  replyLabel.textContent = "Reply to the customer";
  const replyInput = document.createElement("textarea");
  replyInput.id = `reply-input-${ticketId}`;
  replyInput.rows = 3;
  replyInput.maxLength = 5000;
  replyInput.placeholder = "Reply as a support agent… (Enter sends, Shift+Enter adds a line)";
  const replyButton = document.createElement("button");
  replyButton.type = "submit";
  replyButton.className = "primary";
  replyButton.textContent = "Send reply";
  const replyError = document.createElement("p");
  replyError.className = "error";
  replyError.setAttribute("role", "alert");
  replyError.hidden = true;
  replyForm.append(replyLabel, replyInput, replyButton, replyError);
  conversationPanel.append(messageList, aiActiveNote, replyForm);

  // Step 5: the AI activity panel
  const activityPanel = document.createElement("div");
  activityPanel.setAttribute("role", "tabpanel");
  activityPanel.setAttribute("aria-label", "AI activity");
  activityPanel.hidden = true;
  const activityToolbar = document.createElement("div");
  activityToolbar.className = "title-row";
  const activityTitle = document.createElement("h2");
  activityTitle.textContent = "What the AI did on this ticket";
  const activityRefreshButton = document.createElement("button");
  activityRefreshButton.type = "button";
  activityRefreshButton.textContent = "Refresh";
  activityToolbar.append(activityTitle, activityRefreshButton);
  const activityArea = document.createElement("div");
  activityArea.textContent = "Loading AI activity…";
  activityPanel.append(activityToolbar, activityArea);

  container.append(titleRow, approvalLink, loadError, facts, actions, tabList, conversationPanel, activityPanel);

  // Step 6: switching tabs (the conversation is shown first)
  let activeTab = "conversation";
  const showTab = (tab) => {
    activeTab = tab;
    conversationTab.setAttribute("aria-selected", String(tab === "conversation"));
    activityTab.setAttribute("aria-selected", String(tab === "activity"));
    conversationPanel.hidden = tab !== "conversation" || tabList.hidden;
    activityPanel.hidden = tab !== "activity" || tabList.hidden;
  };
  conversationTab.addEventListener("click", () => showTab("conversation"));
  activityTab.addEventListener("click", () => showTab("activity"));

  // Step 7: load the ticket: header, actions and new messages (first time, on events, after actions)
  let currentTicket = null;
  const shownMessageIds = new Set();
  let latestTicketLoad = 0;
  const loadTicket = async () => {
    const thisLoad = ++latestTicketLoad;
    let result;
    try {
      result = await apiRequest("GET", ticketPath);
    } catch (error) {
      if (thisLoad !== latestTicketLoad) {
        return;
      }
      // Step 7a: an unknown (404) or malformed (422) id is "not found"
      if (currentTicket === null) {
        title.textContent =
          error.status === 404 || error.status === 422 ? "Ticket not found" : "Could not load this ticket";
        loadError.textContent = error.status === 404 || error.status === 422 ? "This ticket does not exist." : error.message;
      } else {
        loadError.textContent = `Could not refresh the ticket: ${error.message}`;
      }
      loadError.hidden = false;
      return;
    }
    if (thisLoad !== latestTicketLoad) {
      return; // a newer load is already on its way
    }
    const ticket = result.ticket;
    currentTicket = ticket;
    loadError.hidden = true;

    // Step 7b: title, status and facts
    title.textContent = ticket.subject;
    statusBadge.textContent = ticketStatusLabel(ticket.status);
    statusBadge.className = `badge status-${ticket.status}`;
    statusBadge.hidden = false;
    factValues.customer.textContent = ticket.customer_email;
    if (ticket.assigned_agent_id === currentUser.id) {
      factValues.assignment.textContent = "You";
    } else if (ticket.assigned_agent_id) {
      factValues.assignment.textContent = "Another staff member";
    } else {
      factValues.assignment.textContent = "Unassigned";
    }
    if (!ticket.taken_over_by) {
      factValues.takeover.textContent = "Not taken over";
    } else if (ticket.taken_over_by === currentUser.id) {
      factValues.takeover.textContent = `Taken over by you, ${formatDateTime(ticket.taken_over_at)}`;
    } else {
      factValues.takeover.textContent = `Taken over by another staff member, ${formatDateTime(ticket.taken_over_at)}`;
    }
    factValues.created.textContent = formatDateTime(ticket.created_at);
    factValues.updated.textContent = formatDateTime(ticket.updated_at);
    facts.hidden = false;

    // Step 7c: which actions make sense now
    const isAdmin = currentUser.role === "admin";
    const takenOverByMe = ticket.taken_over_by === currentUser.id;
    const takenOverByOther = Boolean(ticket.taken_over_by) && !takenOverByMe;
    assignButton.hidden = ticket.assigned_agent_id === currentUser.id;
    takeOverButton.hidden = !(!ticket.taken_over_by || (isAdmin && takenOverByOther));
    releaseButton.hidden = !(takenOverByMe || (isAdmin && takenOverByOther));
    statusButton.disabled = statusSelect.value === "" || statusSelect.value === ticket.status;
    takeoverNote.hidden = !(takenOverByOther && !isAdmin);
    takeoverNote.textContent =
      "Another staff member has taken over this conversation. Only they or an admin can release it.";
    actions.hidden = false;

    // Step 7d: show the tabs; the "AI is active" note only while nobody has taken over
    tabList.hidden = false;
    aiActiveNote.hidden = Boolean(ticket.taken_over_by);
    showTab(activeTab);

    // Step 7e: add only the messages we have not shown yet (messages never change once posted)
    let addedCount = 0;
    for (const message of result.messages) {
      if (shownMessageIds.has(message.id)) {
        continue;
      }
      shownMessageIds.add(message.id);
      addedCount = addedCount + 1;

      const item = document.createElement("li");
      item.className = `message from-${message.sender_type}`;
      const sender = document.createElement("span");
      sender.className = "sender";
      if (message.sender_type === "customer") {
        sender.textContent = "Customer";
      } else if (message.sender_type === "ai") {
        sender.textContent = "AI assistant";
      } else if (message.sender_type === "agent") {
        sender.textContent = message.sender_id === currentUser.id ? "Support agent (you)" : "Support agent";
      } else {
        sender.textContent = "System";
      }
      const text = document.createElement("p");
      text.className = "message-text";
      text.textContent = message.content;
      const time = document.createElement("time");
      time.className = "muted small";
      time.dateTime = message.created_at;
      time.textContent = formatDateTime(message.created_at);
      item.append(sender, text, time);
      messageList.append(item);
    }
    if (addedCount > 0) {
      messageList.scrollTop = messageList.scrollHeight;
    }
  };

  // Step 8: a pending approval for this ticket gets a link at the top
  let latestApprovalLoad = 0;
  const loadPendingApproval = async () => {
    const thisLoad = ++latestApprovalLoad;
    try {
      const pending = await apiRequest("GET", "/api/tickets/approvals?status=pending");
      if (thisLoad !== latestApprovalLoad) {
        return;
      }
      const forThisTicket = pending.filter((approval) => approval.ticket_id === ticketId);
      if (forThisTicket.length === 0) {
        approvalLink.hidden = true;
        return;
      }
      const approval = forThisTicket[0];
      approvalLink.textContent = `A refund of ${formatMoney(approval.amount)} for ${approval.summary} is waiting for approval. Review it in Approvals →`;
      approvalLink.hidden = false;
    } catch (error) {
      // only a shortcut; the Approvals page shows the real list and its errors
      approvalLink.hidden = true;
    }
  };

  // Step 9: load the AI activity; redraw only when something changed, and keep open result blocks open
  let lastActivityJson = "";
  const openResultKeys = new Set();
  let latestActivityLoad = 0;
  const loadActivity = async () => {
    if (!container.isConnected) {
      return; // the ticket was closed (a delayed refresh after leaving)
    }
    const thisLoad = ++latestActivityLoad;
    let runs;
    try {
      runs = await apiRequest("GET", `/api/agent/tickets/${encodeURIComponent(ticketId)}/activity`);
    } catch (error) {
      if (thisLoad !== latestActivityLoad) {
        return;
      }
      lastActivityJson = "";
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent = `Could not load the AI activity: ${error.message}`;
      activityArea.replaceChildren(errorText);
      return;
    }
    if (thisLoad !== latestActivityLoad) {
      return;
    }
    const runsJson = JSON.stringify(runs);
    if (runsJson === lastActivityJson) {
      return;
    }
    lastActivityJson = runsJson;

    // Step 9a: empty state
    if (runs.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "The AI has not run on this ticket yet.";
      activityArea.replaceChildren(empty);
      return;
    }

    // Step 9b: one card per run, oldest first (the API's order)
    const runList = document.createElement("ol");
    runList.className = "run-list";
    for (const run of runs) {
      const card = document.createElement("li");
      card.className = `run run-${run.status}`;

      // Step 9c: time, run type and status
      const runHeader = document.createElement("div");
      runHeader.className = "run-header";
      const runTime = document.createElement("time");
      runTime.dateTime = run.started_at;
      runTime.textContent = formatDateTime(run.started_at);
      const runType = document.createElement("strong");
      runType.textContent = RUN_TYPE_LABELS[run.run_type] || run.run_type;
      const runStatus = document.createElement("span");
      runStatus.className = `badge run-status-${run.status}`;
      runStatus.textContent = RUN_STATUS_LABELS[run.status] || run.status;
      runHeader.append(runTime, runType, runStatus);
      card.append(runHeader);

      // Step 9d: the skip reason or note with a short explanation (the pause note has its own line below)
      const replyDiscarded = Boolean(run.note && run.note.includes("reply_discarded_takeover"));
      if (run.note && !run.note.startsWith("paused for approval")) {
        const noteLine = document.createElement("p");
        noteLine.className = "run-note";
        const noteCode = document.createElement("code");
        noteCode.textContent = run.note;
        let explanation = NOTE_EXPLANATIONS[run.note];
        if (!explanation && replyDiscarded) {
          explanation = NOTE_EXPLANATIONS.reply_discarded_takeover;
        } else if (!explanation && run.status === "failed") {
          explanation = "The run failed; the customer got the fallback message.";
        }
        noteLine.append(noteCode);
        if (explanation) {
          noteLine.append(` ${explanation}`);
        }
        card.append(noteLine);
      }

      // Step 9e: escalation reason and the pause for approval
      if (run.escalation_reason) {
        const escalation = document.createElement("p");
        escalation.className = "escalation";
        escalation.textContent = `Escalated to a human: ${run.escalation_reason}`;
        card.append(escalation);
      }
      if (run.paused_for_approval_id) {
        const paused = document.createElement("p");
        paused.className = "paused";
        paused.textContent = "Paused waiting for approval";
        card.append(paused);
      }

      // Step 9f: tool calls in order, with tier, arguments and a collapsible result
      if (run.tool_calls.length > 0) {
        const toolsTitle = document.createElement("h3");
        toolsTitle.textContent = `Tool calls (${run.tool_calls.length})`;
        const toolList = document.createElement("ol");
        toolList.className = "tool-list";
        run.tool_calls.forEach((toolCall, index) => {
          const toolItem = document.createElement("li");
          toolItem.className = toolCall.is_error ? "tool-call tool-error" : "tool-call";
          const toolHeader = document.createElement("div");
          toolHeader.className = "tool-header";
          const step = document.createElement("span");
          step.className = "muted small";
          step.textContent = `Step ${toolCall.step}`;
          const toolName = document.createElement("code");
          toolName.className = "tool-name";
          toolName.textContent = toolCall.tool;
          const tier = document.createElement("span");
          tier.className = `badge tier-${toolCall.tier}`;
          tier.textContent = tierLabel(toolCall.tier);
          toolHeader.append(step, toolName, tier);
          if (toolCall.is_error) {
            const errorBadge = document.createElement("span");
            errorBadge.className = "badge tool-error-badge";
            errorBadge.textContent = "Error";
            toolHeader.append(errorBadge);
          }
          const argumentsBlock = document.createElement("pre");
          argumentsBlock.className = "code-block";
          argumentsBlock.textContent = JSON.stringify(toolCall.arguments, null, 2);

          const resultDetails = document.createElement("details");
          const resultKey = `${run.id}:${index}`;
          resultDetails.open = openResultKeys.has(resultKey);
          resultDetails.addEventListener("toggle", () => {
            if (resultDetails.open) {
              openResultKeys.add(resultKey);
            } else {
              openResultKeys.delete(resultKey);
            }
          });
          const resultSummary = document.createElement("summary");
          resultSummary.textContent = toolCall.is_error ? "Error result" : "Result";
          const resultBlock = document.createElement("pre");
          resultBlock.className = "code-block";
          resultBlock.textContent = toolCall.result === null ? "(no result)" : toolCall.result;
          resultDetails.append(resultSummary, resultBlock);

          toolItem.append(toolHeader, argumentsBlock, resultDetails);
          toolList.append(toolItem);
        });
        card.append(toolsTitle, toolList);
      }

      // Step 9g: knowledge-base chunks the searches returned
      if (run.retrieved_chunks.length > 0) {
        const chunksTitle = document.createElement("h3");
        chunksTitle.textContent = `Knowledge retrieved (${run.retrieved_chunks.length})`;
        const chunkList = document.createElement("ul");
        chunkList.className = "chunk-list";
        for (const chunk of run.retrieved_chunks) {
          const chunkItem = document.createElement("li");
          const chunkName = document.createElement("span");
          chunkName.textContent = `${chunk.title} › ${chunk.section}`;
          const score = document.createElement("span");
          score.className = "badge";
          score.textContent = `score ${Number(chunk.score).toFixed(2)}`;
          chunkItem.append(chunkName, " ", score);
          chunkList.append(chunkItem);
        }
        card.append(chunksTitle, chunkList);
      }

      // Step 9h: the reply text (and whether it was posted)
      if (run.reply_text) {
        const replyTitle = document.createElement("h3");
        replyTitle.textContent = "Reply";
        const replyText = document.createElement("p");
        replyText.className = "run-reply";
        replyText.textContent = run.reply_text;
        card.append(replyTitle);
        if (replyDiscarded) {
          const notPosted = document.createElement("p");
          notPosted.className = "not-posted";
          notPosted.textContent = "Not posted: a human took over";
          card.append(notPosted);
        }
        card.append(replyText);
      }

      // Step 9i: model, tokens and duration
      const meta = document.createElement("p");
      meta.className = "muted small";
      const metaParts = [];
      if (run.model_name) {
        metaParts.push(`Model ${run.model_name}`);
      }
      metaParts.push(`Tokens ${run.input_tokens} in / ${run.output_tokens} out`);
      if (run.duration_ms !== null) {
        metaParts.push(`Duration ${(run.duration_ms / 1000).toFixed(1)} s`);
      }
      meta.textContent = metaParts.join(" · ");
      card.append(meta);

      runList.append(card);
    }
    activityArea.replaceChildren(runList);
  };

  loadTicket();
  loadPendingApproval();
  loadActivity();

  // Step 10: actions: send the request, show a readable error if it fails, then refresh the ticket
  const runAction = async (button, method, path, body) => {
    actionError.hidden = true;
    button.disabled = true;
    try {
      await apiRequest(method, path, body);
    } catch (error) {
      actionError.textContent = error.message;
      actionError.hidden = false;
    } finally {
      button.disabled = false;
    }
    await loadTicket();
  };
  assignButton.addEventListener("click", () => runAction(assignButton, "POST", `${ticketPath}/assign`));
  takeOverButton.addEventListener("click", () => runAction(takeOverButton, "POST", `${ticketPath}/takeover`));
  releaseButton.addEventListener("click", () => runAction(releaseButton, "POST", `${ticketPath}/release`));
  statusSelect.addEventListener("change", () => {
    statusButton.disabled = statusSelect.value === "" || (currentTicket && statusSelect.value === currentTicket.status);
  });
  statusForm.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    if (statusSelect.value === "") {
      return;
    }
    await runAction(statusButton, "PATCH", `${ticketPath}/status`, { status: statusSelect.value });
    statusSelect.value = "";
    statusButton.disabled = true;
  });

  // Step 11: send a reply as the support agent; keep the text if it fails
  replyForm.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const content = replyInput.value.trim();
    if (content === "" || replyButton.disabled) {
      return;
    }
    replyError.hidden = true;
    replyButton.disabled = true;
    replyInput.disabled = true;
    replyButton.textContent = "Sending…";
    try {
      await apiRequest("POST", `${ticketPath}/messages`, { content });
      replyInput.value = "";
      await loadTicket();
    } catch (error) {
      replyError.textContent = `Your reply was not sent: ${error.message}`;
      replyError.hidden = false;
    } finally {
      replyButton.disabled = false;
      replyInput.disabled = false;
      replyButton.textContent = "Send reply";
      replyInput.focus();
    }
  });
  replyInput.addEventListener("keydown", (keyEvent) => {
    if (keyEvent.key === "Enter" && !keyEvent.shiftKey && !keyEvent.isComposing) {
      keyEvent.preventDefault();
      replyForm.requestSubmit();
    }
  });
  activityRefreshButton.addEventListener("click", () => loadActivity());

  // Step 12: what main.js calls with a group of events (null after a reconnect)
  let delayedActivityTimer = null;
  return (events) => {
    // Step 12a: a reconnect: refetch everything
    if (events === null) {
      loadTicket();
      loadPendingApproval();
      loadActivity();
      return;
    }

    // Step 12b: only events about this ticket matter
    const ourEvents = events.filter((event) => event.data && event.data.ticket_id === ticketId);
    if (ourEvents.some((event) => event.event_type.startsWith("ticket.") || event.event_type.startsWith("approval."))) {
      loadTicket();
    }
    if (ourEvents.some((event) => event.event_type.startsWith("approval."))) {
      loadPendingApproval();
    }

    // Step 12c: a new message may come with a new run: look now, and again shortly after
    if (ourEvents.some((event) => event.event_type === "ticket.message_created")) {
      loadActivity();
      clearTimeout(delayedActivityTimer);
      delayedActivityTimer = setTimeout(loadActivity, DELAYED_ACTIVITY_REFRESH_MS);
    }
  };
}
