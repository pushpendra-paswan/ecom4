// Placeholder: proves that static files and API routing through Nginx both work
const healthResult = document.getElementById("health-result");

try {
  // Step 1: call the ticket service health endpoint through Nginx
  const response = await fetch("/api/tickets/health");
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  // Step 2: show what the service answered
  const data = await response.json();
  healthResult.textContent = `${data.service}: ${data.status}`;
} catch (error) {
  // Step 3: show the error instead of failing silently
  healthResult.textContent = `Error: ${error.message}`;
}
