// The one place that talks to the REST API, plus the token kept in sessionStorage.
const TOKEN_KEY = "agent_token";

// main.js sets this; it is called when the API says the session is over (401)
let sessionEndedHandler = null;

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function saveToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function setSessionEndedHandler(handler) {
  sessionEndedHandler = handler;
}

// Send a request through Nginx and return the parsed JSON body.
// Throws an Error with a readable message (and error.status) when it fails.
export async function apiRequest(method, path, body) {
  // Step 1: build the request; add the token when we have one
  const headers = {};
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const options = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  // Step 2: send it; a network failure gets its own message
  let response;
  try {
    response = await fetch(path, options);
  } catch (networkError) {
    throw new Error("Could not reach the server. Check your connection and try again.");
  }

  // Step 3: read the body as JSON if there is one (Nginx error pages are HTML)
  let data = null;
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    data = await response.json();
  }

  // Step 4: a 401 on anything but the login call means the token is missing or expired
  if (response.status === 401 && path !== "/api/identity/login") {
    if (sessionEndedHandler) {
      sessionEndedHandler();
    }
    const sessionError = new Error("Your session has ended. Please sign in again.");
    sessionError.status = 401;
    throw sessionError;
  }

  // Step 5: success
  if (response.ok) {
    return data;
  }

  // Step 6: turn the error body into one readable sentence
  let message = `Something went wrong (HTTP ${response.status}). Please try again.`;
  if (data && typeof data.detail === "string") {
    message = data.detail;
  } else if (data && Array.isArray(data.detail)) {
    // FastAPI validation errors (422): "field: problem" for each one
    const problems = data.detail.map((problem) => {
      const field = Array.isArray(problem.loc) ? problem.loc[problem.loc.length - 1] : "";
      return field ? `${field}: ${problem.msg}` : problem.msg;
    });
    message = problems.join(". ");
  } else if (response.status >= 500) {
    message = `The service is not available right now (HTTP ${response.status}). Please try again shortly.`;
  }
  const requestError = new Error(message);
  requestError.status = response.status;
  throw requestError;
}
