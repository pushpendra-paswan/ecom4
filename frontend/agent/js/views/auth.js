// Sign in for support staff (no registration: staff accounts come from the seed).
import { apiRequest, saveToken } from "../api.js";

// options: message (shown above the form), onSignedIn() (main.js loads the profile and opens the dashboard)
export function renderAuth(container, options) {
  // Step 1: the card, title and the message from main.js (session ended, customer account)
  const card = document.createElement("div");
  card.className = "card auth-card";
  const title = document.createElement("h1");
  title.textContent = "Support dashboard";
  card.append(title);

  const notice = document.createElement("p");
  notice.className = "notice";
  notice.setAttribute("role", "status");
  notice.textContent = options.message || "";
  notice.hidden = !options.message;
  card.append(notice);

  // Step 2: the sign-in form
  const form = document.createElement("form");
  form.className = "stack";

  const emailLabel = document.createElement("label");
  emailLabel.textContent = "Email";
  const emailInput = document.createElement("input");
  emailInput.type = "email";
  emailInput.autocomplete = "username";
  emailInput.required = true;
  emailLabel.append(emailInput);

  const passwordLabel = document.createElement("label");
  passwordLabel.textContent = "Password";
  const passwordInput = document.createElement("input");
  passwordInput.type = "password";
  passwordInput.autocomplete = "current-password";
  passwordInput.required = true;
  passwordLabel.append(passwordInput);

  const errorText = document.createElement("p");
  errorText.className = "error";
  errorText.setAttribute("role", "alert");
  errorText.hidden = true;

  const submitButton = document.createElement("button");
  submitButton.type = "submit";
  submitButton.className = "primary";
  submitButton.textContent = "Sign in";

  form.append(emailLabel, passwordLabel, errorText, submitButton);
  card.append(form);

  // Step 3: the demo accounts hint (this is a learning project)
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent =
    "Demo staff accounts: agent1@example.com, agent2@example.com or admin@example.com, password Password123!";
  const tabsHint = document.createElement("p");
  tabsHint.className = "hint";
  tabsHint.textContent =
    "Each browser tab has its own session, so a customer (in the customer app) and one or more agents can be signed in side by side in different tabs.";
  card.append(hint, tabsHint);
  container.append(card);
  emailInput.focus();

  // Step 4: submit: log in, save the token and hand over to main.js (which checks the role)
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    errorText.hidden = true;
    submitButton.disabled = true;
    submitButton.textContent = "Signing in…";
    try {
      const login = await apiRequest("POST", "/api/identity/login", {
        email: emailInput.value.trim(),
        password: passwordInput.value,
      });
      saveToken(login.access_token);
      await options.onSignedIn();
    } catch (error) {
      errorText.textContent = error.message;
      errorText.hidden = false;
      submitButton.disabled = false;
      submitButton.textContent = "Sign in";
    }
  });
}
