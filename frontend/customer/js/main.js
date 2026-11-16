// Start-up, the hash router, the header, sign out, and passing realtime events to the open view.
import { apiRequest, clearToken, getToken, setSessionEndedHandler } from "./api.js";
import { connectRealtime, disconnectRealtime } from "./realtime.js";
import { renderAuth } from "./views/auth.js";
import { renderChat } from "./views/chat.js";
import { renderOrderDetail, renderOrderList } from "./views/orders.js";
import { renderNewTicket, renderTicketList } from "./views/tickets.js";

const SESSION_ENDED_MESSAGE = "Your session has ended. Please sign in again.";
const STAFF_MESSAGE = "This app is for customers. Support staff should use the agent dashboard.";

const mainElement = document.getElementById("app");
const headerElement = document.getElementById("app-header");
const userNameElement = document.getElementById("user-name");
const connectionStatusElement = document.getElementById("connection-status");
const signOutButton = document.getElementById("sign-out-button");
const navLinks = document.querySelectorAll("[data-nav]");

let currentUser = null;
let loginMessage = "";
// The open view's refresh function: called with an event, or with null after a reconnect
let currentViewUpdate = null;

// Change the hash, or re-render if we are already there (no hashchange would fire)
function goTo(hash) {
  if (window.location.hash === hash) {
    showCurrentRoute();
  } else {
    window.location.hash = hash;
  }
}

// Used by the Sign out button, a 401, a 4401 socket close and a staff account
function signOut(message) {
  // Step 1: forget the session and close the socket
  disconnectRealtime();
  clearToken();
  currentUser = null;
  currentViewUpdate = null;

  // Step 2: hide the header
  headerElement.hidden = true;
  connectionStatusElement.hidden = true;

  // Step 3: show the sign-in page with the message
  loginMessage = message;
  goTo("#/login");
}

// After a sign-in, or on page load with a saved token: load the profile and connect live updates
async function startSession() {
  // Step 1: load the profile (a 401 here signs out through the session-ended handler)
  const user = await apiRequest("GET", "/api/identity/me");

  // Step 2: this app is only for customers
  if (user.role !== "customer") {
    signOut(STAFF_MESSAGE);
    return false;
  }

  // Step 3: show the header with the customer's name
  currentUser = user;
  loginMessage = "";
  userNameElement.textContent = user.full_name;
  headerElement.hidden = false;

  // Step 4: open the WebSocket; events refresh whatever view is open
  connectRealtime({
    onEvent: (event) => {
      if (currentViewUpdate && event.event_type.startsWith("ticket.")) {
        currentViewUpdate(event);
      }
    },
    onReconnected: () => {
      // events sent while we were disconnected are lost, so refetch the view
      if (currentViewUpdate) {
        currentViewUpdate(null);
      }
    },
    onConnectionChange: (isConnected) => {
      connectionStatusElement.hidden = isConnected;
    },
    onUnauthorized: () => signOut(SESSION_ENDED_MESSAGE),
  });
  return true;
}

// The router: #/login, #/tickets, #/tickets/<id>, #/new-ticket, #/orders, #/orders/<order_number>
function showCurrentRoute() {
  // Step 1: split the hash into path parts and query parameters
  const hash = window.location.hash.slice(1) || "/tickets";
  const [path, queryString] = hash.split("?");
  const parts = path.split("/").filter((part) => part !== "");
  const query = new URLSearchParams(queryString || "");

  // Step 2: a fresh container for the view (late responses of the old view land in the detached one)
  const viewElement = document.createElement("section");
  mainElement.replaceChildren(viewElement);
  currentViewUpdate = null;

  // Step 3: signed out: only the sign-in page
  if (!currentUser) {
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

  // Step 4: mark the active navigation link
  const section = parts[0] === "orders" ? "orders" : "tickets";
  navLinks.forEach((link) => {
    if (link.dataset.nav === section) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });

  // Step 5: pick the view
  if (parts[0] === "tickets" && parts[1]) {
    currentViewUpdate = renderChat(viewElement, decodeURIComponent(parts[1]));
  } else if (parts[0] === "new-ticket") {
    renderNewTicket(viewElement, query.get("order"));
  } else if (parts[0] === "orders" && parts[1]) {
    currentViewUpdate = renderOrderDetail(viewElement, decodeURIComponent(parts[1]));
  } else if (parts[0] === "orders") {
    currentViewUpdate = renderOrderList(viewElement);
  } else if (parts[0] === "tickets") {
    currentViewUpdate = renderTicketList(viewElement);
  } else {
    // #/login while signed in, or an unknown route
    goTo("#/tickets");
  }
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
