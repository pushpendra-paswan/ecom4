// Start-up, the hash router, the header with the pending-approval count, sign out,
// and passing (grouped) realtime events to the open views.
import { apiRequest, clearToken, getToken, setSessionEndedHandler } from "./api.js";
import { connectRealtime, disconnectRealtime } from "./realtime.js";
import { renderApprovals } from "./views/approvals.js";
import { renderAuth } from "./views/auth.js";
import { renderQueue } from "./views/queue.js";
import { renderTicket } from "./views/ticket.js";

const SESSION_ENDED_MESSAGE = "Your session has ended. Please sign in again.";
const CUSTOMER_MESSAGE = "This dashboard is for support staff. Customers should use the customer app.";
const STAFF_ROLES = ["agent", "admin"];
// Events that arrive within this time of each other are handled as one group (one refetch per view)
const EVENT_GROUP_DELAY_MS = 500;

const mainElement = document.getElementById("app");
const headerElement = document.getElementById("app-header");
const userEmailElement = document.getElementById("user-email");
const userRoleElement = document.getElementById("user-role");
const pendingCountElement = document.getElementById("pending-count");
const connectionStatusElement = document.getElementById("connection-status");
const signOutButton = document.getElementById("sign-out-button");
const navLinks = document.querySelectorAll("[data-nav]");

let currentUser = null;
let loginMessage = "";

// The open views. Each refresh function is called with a list of events, or with null after a reconnect.
let queueView = null; // { refresh, markSelected } while the tickets layout is shown
let ticketRefresh = null; // the open ticket's refresh function
let approvalsRefresh = null; // the approvals page's refresh function
let workspaceElement = null; // the tickets layout (queue beside the detail)
let detailPane = null;

// Events waiting to be handled as one group
let pendingEvents = [];
let eventGroupTimer = null;
let latestCountLoad = 0;

// Change the hash, or re-render if we are already there (no hashchange would fire)
function goTo(hash) {
  if (window.location.hash === hash) {
    showCurrentRoute();
  } else {
    window.location.hash = hash;
  }
}

// Forget every open view (used when the page is replaced)
function clearViews() {
  queueView = null;
  ticketRefresh = null;
  approvalsRefresh = null;
  workspaceElement = null;
  detailPane = null;
}

// Used by the Sign out button, a 401, a 4401 socket close and a customer account
function signOut(message) {
  // Step 1: forget the session, the waiting events and close the socket
  disconnectRealtime();
  clearToken();
  currentUser = null;
  clearTimeout(eventGroupTimer);
  pendingEvents = [];
  clearViews();

  // Step 2: hide the header
  headerElement.hidden = true;
  connectionStatusElement.hidden = true;

  // Step 3: show the sign-in page with the message
  loginMessage = message;
  goTo("#/login");
}

// The number next to "Approvals" (on start, approval events and reconnects)
async function refreshPendingCount() {
  const thisLoad = ++latestCountLoad;
  try {
    const pending = await apiRequest("GET", "/api/tickets/approvals?status=pending");
    if (thisLoad !== latestCountLoad) {
      return;
    }
    pendingCountElement.textContent = String(pending.length);
    pendingCountElement.title = `${pending.length} pending approval${pending.length === 1 ? "" : "s"}`;
    pendingCountElement.hidden = false;
  } catch (error) {
    // the count is a hint only; the approvals page shows the real error
    pendingCountElement.hidden = true;
  }
}

// Hand a group of events (or null after a reconnect) to every open view; each view decides what to refetch
function refreshViews(events) {
  // Step 1: the views
  if (queueView) {
    queueView.refresh(events);
  }
  if (ticketRefresh) {
    ticketRefresh(events);
  }
  if (approvalsRefresh) {
    approvalsRefresh(events);
  }

  // Step 2: the pending count changes only with approval events
  if (events === null || events.some((event) => event.event_type.startsWith("approval."))) {
    refreshPendingCount();
  }
}

// After a sign-in, or on page load with a saved token: load the profile and connect live updates
async function startSession() {
  // Step 1: load the profile (a 401 here signs out through the session-ended handler)
  const user = await apiRequest("GET", "/api/identity/me");

  // Step 2: this dashboard is only for support staff
  if (!STAFF_ROLES.includes(user.role)) {
    signOut(CUSTOMER_MESSAGE);
    return false;
  }

  // Step 3: show the header with the email, role and pending count
  currentUser = user;
  loginMessage = "";
  userEmailElement.textContent = user.email;
  userRoleElement.textContent = user.role;
  headerElement.hidden = false;
  refreshPendingCount();

  // Step 4: open the WebSocket; events are collected and handled together about half a second after the last one
  connectRealtime({
    onEvent: (event) => {
      pendingEvents.push(event);
      clearTimeout(eventGroupTimer);
      eventGroupTimer = setTimeout(() => {
        const events = pendingEvents;
        pendingEvents = [];
        refreshViews(events);
      }, EVENT_GROUP_DELAY_MS);
    },
    onReconnected: () => {
      // events sent while we were disconnected are lost, so refetch everything that is open
      refreshViews(null);
    },
    onConnectionChange: (isConnected) => {
      connectionStatusElement.hidden = isConnected;
    },
    onUnauthorized: () => signOut(SESSION_ENDED_MESSAGE),
  });
  return true;
}

// The router: #/login, #/tickets, #/tickets/<id>, #/approvals
function showCurrentRoute() {
  // Step 1: split the hash into path parts
  const hash = window.location.hash.slice(1) || "/tickets";
  const parts = hash.split("?")[0].split("/").filter((part) => part !== "");

  // Step 2: signed out: only the sign-in page
  if (!currentUser) {
    clearViews();
    const viewElement = document.createElement("section");
    mainElement.replaceChildren(viewElement);
    renderAuth(viewElement, {
      message: loginMessage,
      onSignedIn: async () => {
        const started = await startSession();
        if (started) {
          goTo("#/tickets");
        }
      },
    });
    return;
  }

  // Step 3: mark the active navigation link
  const section = parts[0] === "approvals" ? "approvals" : "tickets";
  navLinks.forEach((link) => {
    if (link.dataset.nav === section) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });

  // Step 4: tickets: keep the queue (and its filters) when moving between tickets, replace only the detail
  if (parts[0] === "tickets") {
    const ticketId = parts[1] ? decodeURIComponent(parts[1]) : null;
    if (!workspaceElement) {
      clearViews();
      workspaceElement = document.createElement("div");
      workspaceElement.className = "workspace";
      const queuePane = document.createElement("section");
      queuePane.className = "queue-pane";
      queuePane.setAttribute("aria-label", "Ticket queue");
      detailPane = document.createElement("div");
      detailPane.className = "detail-pane";
      workspaceElement.append(queuePane, detailPane);
      mainElement.replaceChildren(workspaceElement);
      queueView = renderQueue(queuePane, currentUser);
    }

    // Step 4a: a fresh container for the detail (late responses of the old ticket land in the detached one)
    const detailElement = document.createElement("section");
    detailPane.replaceChildren(detailElement);
    workspaceElement.classList.toggle("has-selection", ticketId !== null);
    queueView.markSelected(ticketId);
    if (ticketId) {
      ticketRefresh = renderTicket(detailElement, ticketId, currentUser);
    } else {
      ticketRefresh = null;
      const placeholder = document.createElement("p");
      placeholder.className = "empty card";
      placeholder.textContent = "Select a ticket to see its conversation and AI activity.";
      detailElement.append(placeholder);
    }
    return;
  }

  // Step 5: approvals
  if (parts[0] === "approvals") {
    clearViews();
    const viewElement = document.createElement("section");
    mainElement.replaceChildren(viewElement);
    approvalsRefresh = renderApprovals(viewElement, currentUser);
    return;
  }

  // Step 6: #/login while signed in, or an unknown route
  goTo("#/tickets");
}

// Start-up
// Step 1: a 401 anywhere ends the session
setSessionEndedHandler(() => {
  if (currentUser || getToken()) {
    signOut(SESSION_ENDED_MESSAGE);
  }
});

// Step 2: header buttons and navigation
signOutButton.addEventListener("click", () => signOut(""));
window.addEventListener("hashchange", showCurrentRoute);

// Step 3: resume a saved session (page reload), then show the page in the address bar
if (getToken()) {
  mainElement.textContent = "Loading…";
  try {
    await startSession();
  } catch (error) {
    // a 401 already signed out; anything else: show the error on the sign-in page
    if (error.status !== 401) {
      clearToken();
      loginMessage = error.message;
    }
  }
}
showCurrentRoute();
