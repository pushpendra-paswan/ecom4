// Sign in and register, switchable on the same page.
import { apiRequest, saveToken } from "../api.js";

// options: message (shown above the form), onSignedIn() (main.js loads the profile and opens the app)
export function renderAuth(container, options) {
  // Step 1: the card, title and the message from main.js (session ended, staff account)
  const card = document.createElement("div");
  card.className = "card auth-card";
  const title = document.createElement("h1");
  title.textContent = "Customer support";
  card.append(title);

  const notice = document.createElement("p");
  notice.className = "notice";
  notice.setAttribute("role", "status");
  notice.textContent = options.message || "";
  notice.hidden = !options.message;
  card.append(notice);

  // Step 2: the two tabs that switch between the forms
  const switcher = document.createElement("div");
  switcher.className = "auth-switch";
  const signInTab = document.createElement("button");
  signInTab.type = "button";
  signInTab.textContent = "Sign in";
  const registerTab = document.createElement("button");
  registerTab.type = "button";
  registerTab.textContent = "Create account";
  switcher.append(signInTab, registerTab);
  card.append(switcher);

  // Step 3: one form; the full name field is shown only when registering
  const form = document.createElement("form");
  form.className = "stack";

  const nameLabel = document.createElement("label");
  nameLabel.textContent = "Full name";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.autocomplete = "name";
  nameInput.maxLength = 200;
  nameLabel.append(nameInput);

  const emailLabel = document.createElement("label");
  emailLabel.textContent = "Email";
  const emailInput = document.createElement("input");
  emailInput.type = "email";
  emailInput.autocomplete = "email";
  emailInput.required = true;
  emailLabel.append(emailInput);

  const passwordLabel = document.createElement("label");
  passwordLabel.textContent = "Password";
  const passwordInput = document.createElement("input");
  passwordInput.type = "password";
  passwordInput.required = true;
  passwordLabel.append(passwordInput);

  const errorText = document.createElement("p");
  errorText.className = "error";
  errorText.setAttribute("role", "alert");
  errorText.hidden = true;

  const submitButton = document.createElement("button");
  submitButton.type = "submit";
  submitButton.className = "primary";

  form.append(nameLabel, emailLabel, passwordLabel, errorText, submitButton);
  card.append(form);

  // Step 4: the demo accounts hint (this is a learning project)
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent =
    "Demo accounts: customer1@example.com, customer2@example.com or customer3@example.com, password Password123!";
  card.append(hint);
  container.append(card);

  // Step 5: switching modes changes the visible field, the labels and the password rules
  let mode = "signin";
  const showMode = (newMode) => {
    mode = newMode;
    const isRegister = mode === "register";
    nameLabel.hidden = !isRegister;
    nameInput.required = isRegister;
    passwordInput.autocomplete = isRegister ? "new-password" : "current-password";
    passwordInput.minLength = isRegister ? 8 : 0;
    submitButton.textContent = isRegister ? "Create account" : "Sign in";
    signInTab.setAttribute("aria-pressed", String(!isRegister));
    registerTab.setAttribute("aria-pressed", String(isRegister));
    errorText.hidden = true;
  };
  signInTab.addEventListener("click", () => showMode("signin"));
  registerTab.addEventListener("click", () => showMode("register"));
  showMode("signin");

  // Step 6: submit: register first if needed, then log in, save the token and hand over to main.js
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    errorText.hidden = true;
    submitButton.disabled = true;
    submitButton.textContent = mode === "register" ? "Creating account…" : "Signing in…";
    try {
      const email = emailInput.value.trim();
      const password = passwordInput.value;
      if (mode === "register") {
        await apiRequest("POST", "/api/identity/register", {
          full_name: nameInput.value.trim(),
          email,
          password,
        });
      }
      const login = await apiRequest("POST", "/api/identity/login", { email, password });
      saveToken(login.access_token);
      await options.onSignedIn();
    } catch (error) {
      errorText.textContent = error.message;
      errorText.hidden = false;
      submitButton.disabled = false;
      submitButton.textContent = mode === "register" ? "Create account" : "Sign in";
    }
  });
}
