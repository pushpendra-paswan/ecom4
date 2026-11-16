// The one WebSocket to the Realtime service: auth, ping, reconnect and duplicate filtering.
// Events are only refresh signals; the views refetch over REST.
import { getToken } from "./api.js";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000];
const LATER_RECONNECT_DELAY_MS = 10000;
const PING_INTERVAL_MS = 25000;
const MAX_REMEMBERED_EVENT_IDS = 500;

let socket = null;
let handlers = null;
let stopped = true;
let pingTimer = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let hasConnectedBefore = false;
const seenEventIds = new Set();

// handlers: onEvent(envelope), onReconnected(), onConnectionChange(isConnected), onUnauthorized()
export function connectRealtime(newHandlers) {
  // Step 1: remember who to tell, and that we want to stay connected
  handlers = newHandlers;
  stopped = false;
  reconnectTimer = null;

  // Step 2: open the socket on the same host, ws:// or wss:// matching the page
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const thisSocket = new WebSocket(`${protocol}//${window.location.host}/ws/connect`);
  socket = thisSocket;

  // Step 3: the first message must be the auth message
  thisSocket.addEventListener("open", () => {
    thisSocket.send(JSON.stringify({ type: "auth", token: getToken() }));
  });

  // Step 4: handle what the server sends
  thisSocket.addEventListener("message", (messageEvent) => {
    let message;
    try {
      message = JSON.parse(messageEvent.data);
    } catch (parseError) {
      return;
    }

    // Step 4a: authenticated: reset the backoff, start pinging, refetch if this is a reconnect
    if (message.type === "auth_ok") {
      reconnectAttempt = 0;
      clearInterval(pingTimer);
      pingTimer = setInterval(() => {
        thisSocket.send(JSON.stringify({ type: "ping" }));
      }, PING_INTERVAL_MS);
      handlers.onConnectionChange(true);
      if (hasConnectedBefore) {
        handlers.onReconnected();
      }
      hasConnectedBefore = true;
      return;
    }

    // Step 4b: an event: skip ids we already passed on (delivery is at-least-once)
    if (message.type === "event" && message.event) {
      const eventId = message.event.event_id;
      if (seenEventIds.has(eventId)) {
        return;
      }
      seenEventIds.add(eventId);
      if (seenEventIds.size > MAX_REMEMBERED_EVENT_IDS) {
        seenEventIds.delete(seenEventIds.values().next().value);
      }
      handlers.onEvent(message.event);
    }
    // pong and error messages need nothing
  });

  // Step 5: on close, end the session (4401) or reconnect with growing delays
  thisSocket.addEventListener("close", (closeEvent) => {
    if (socket !== thisSocket) {
      return; // an old socket we already replaced
    }
    clearInterval(pingTimer);
    socket = null;
    if (stopped) {
      return;
    }
    if (closeEvent.code === 4401) {
      handlers.onUnauthorized();
      return;
    }
    handlers.onConnectionChange(false);
    const delay = RECONNECT_DELAYS_MS[reconnectAttempt] || LATER_RECONNECT_DELAY_MS;
    reconnectAttempt = reconnectAttempt + 1;
    reconnectTimer = setTimeout(() => connectRealtime(handlers), delay);
  });
}

// Called on sign out: close the socket and never reconnect
export function disconnectRealtime() {
  // Step 1: stop every timer and the reconnect loop
  stopped = true;
  clearInterval(pingTimer);
  clearTimeout(reconnectTimer);
  reconnectTimer = null;

  // Step 2: close the socket (its close handler sees `stopped` and does nothing)
  if (socket) {
    socket.close(1000, "Signed out");
    socket = null;
  }

  // Step 3: forget this session's state
  reconnectAttempt = 0;
  hasConnectedBefore = false;
  seenEventIds.clear();
}
