# Checks the human takeover and the AI activity log (M10).
# Run from the repo root on the host, with the stack up and seeded (identity users, knowledge documents) and
# FRESH demo orders:
#   docker compose exec order python seed.py --reset
#   python scripts/check_takeover_activity.py
# Standard library only. Opens tickets as demo customers through the Ticket REST API, waits for the AI, takes
# tickets over and releases them as agent1 (and tries as customer1 and agent2), and reads the AI activity log from
# the Agent REST API (GET /api/agent/tickets/{id}/activity). Prints every step, PASS/FAIL per check and a summary.
# Takes about two minutes.
import json
import time
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8080"
DEMO_PASSWORD = "Password123!"
AGENT1_ID = "00000000-0000-0000-0000-000000000011"
POLL_SECONDS = 2
REPLY_WAIT_SECONDS = 60
# While a ticket is taken over, no AI message may appear within this time
SILENCE_WAIT_SECONDS = 20
# The run row is saved right after the reply is posted; give it a few seconds to be finished
RUN_WAIT_SECONDS = 15
# The system messages the Ticket service posts (services/ticket/app/services/ticket_service.py)
TAKEOVER_MESSAGE = "A support agent has joined the conversation."
RELEASE_MESSAGE = "Our AI assistant is back to help. A support agent can still join at any time."

scenario_results = []


# Sends a JSON request through Nginx and returns the parsed JSON response
def call_api(method: str, path: str, body: dict | None, token: str | None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


# Sends a request that is expected to fail and returns only the HTTP status code
def call_api_status(method: str, path: str, token: str) -> int:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=b"" if method == "POST" else None, headers={"Authorization": f"Bearer {token}"}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


# Polls the ticket until it has more than `known_ai_count` ai messages; returns the newest one ("" on timeout)
def wait_for_ai_reply(token: str, ticket_id: str, known_ai_count: int, wait_seconds: int) -> str:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        ticket = call_api("GET", f"/api/tickets/tickets/{ticket_id}", None, token)
        ai_messages = [message for message in ticket["messages"] if message["sender_type"] == "ai"]
        if len(ai_messages) > known_ai_count:
            return ai_messages[-1]["content"]
    return ""


# Polls the ticket's activity log (as staff) until it has `run_count` runs and none is still "started"
def wait_for_activity(staff_token: str, ticket_id: str, run_count: int) -> list:
    deadline = time.time() + RUN_WAIT_SECONDS
    runs = []
    while time.time() < deadline:
        runs = call_api("GET", f"/api/agent/tickets/{ticket_id}/activity", None, staff_token)
        if len(runs) >= run_count and all(run["status"] != "started" for run in runs):
            return runs
        time.sleep(POLL_SECONDS)
    return runs


# Prints one scenario with a PASS/FAIL line per check and remembers whether all checks passed
def report(name: str, detail: str, checks: list) -> None:
    print(f"=== {name}")
    print(detail)
    for description, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {description}")
    scenario_results.append(all(passed for _, passed in checks))
    print()


print("Before running this, reset the demo orders (fresh dates, no returns or refunds):")
print("  docker compose exec order python seed.py --reset")
print()

# Step 1: log in as the demo users this script needs
tokens = {}
for user in ["customer1", "customer3", "agent1", "agent2"]:
    login = {"email": f"{user}@example.com", "password": DEMO_PASSWORD}
    tokens[user] = call_api("POST", "/api/identity/login", login, None)["access_token"]

# Step 2: scenario 1, a policy question: the activity log shows the search, the retrieved chunks and the reply
question = "How long do I have to return a jacket?"
print("--- 1: customer1 asks a policy question")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Jacket return window", "message": question}, tokens["customer1"])
policy_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer1"], policy_ticket_id, 0, REPLY_WAIT_SECONDS)
runs = wait_for_activity(tokens["agent1"], policy_ticket_id, 1)
run = runs[0] if runs else {}
search_calls = [call for call in run.get("tool_calls", []) if call["tool"] == "search_knowledge_base"]
chunk_titles = sorted({chunk["title"] for chunk in run.get("retrieved_chunks", [])})
report("1. Activity of a policy answer (customer1)", f"Q: {question}\nA: {reply or '(no reply)'}\n"
       f"run: {run.get('status')}, {len(run.get('tool_calls', []))} tool call(s), chunks from {chunk_titles}, "
       f"tokens {run.get('input_tokens')}/{run.get('output_tokens')}, {run.get('duration_ms')} ms, model {run.get('model_name')}", [
    (f"one completed run (got {len(runs)} run(s), status {run.get('status')})", len(runs) == 1 and run.get("status") == "completed"),
    (f"a search_knowledge_base call whose arguments contain only 'query' (got {[call['arguments'] for call in search_calls]})",
     len(search_calls) > 0 and all(list(call["arguments"].keys()) == ["query"] for call in search_calls)),
    ("retrieved_chunks is not empty and includes the Returns Policy",
     len(run.get("retrieved_chunks", [])) > 0 and "Returns Policy" in chunk_titles),
    ("reply_text equals the AI message on the ticket", reply != "" and run.get("reply_text") == reply),
    ("input_tokens and duration_ms are above zero", (run.get("input_tokens") or 0) > 0 and (run.get("duration_ms") or 0) > 0),
])

# Step 3: scenario 2, an order question: the get_order arguments are exactly what the model chose (no customer_id)
question = "Where is my order ORD-1003?"
print("--- 2: customer1 asks about an order")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Order ORD-1003", "message": question}, tokens["customer1"])
order_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer1"], order_ticket_id, 0, REPLY_WAIT_SECONDS)
runs = wait_for_activity(tokens["agent1"], order_ticket_id, 1)
run = runs[0] if runs else {}
get_order_calls = [call for call in run.get("tool_calls", []) if call["tool"] == "get_order"]
report("2. Activity of an order lookup (customer1)", f"Q: {question}\nA: {reply or '(no reply)'}\n"
       f"tool calls: {[(call['step'], call['tool'], call['arguments']) for call in run.get('tool_calls', [])]}", [
    (f"a get_order call with arguments exactly {{'order_number': 'ORD-1003'}} and tier 1 (got {[(call['arguments'], call['tier']) for call in get_order_calls]})",
     any(call["arguments"] == {"order_number": "ORD-1003"} and call["tier"] == 1 for call in get_order_calls)),
    ("no customer_id anywhere in tool_calls", len(run.get("tool_calls", [])) > 0 and "customer_id" not in json.dumps(run.get("tool_calls", []))),
])

# Step 4: scenario 3, agent1 takes over the ticket from scenario 1
print("--- 3: agent1 takes over the policy ticket")
taken_over = call_api("POST", f"/api/tickets/tickets/{policy_ticket_id}/takeover", None, tokens["agent1"])
messages = call_api("GET", f"/api/tickets/tickets/{policy_ticket_id}", None, tokens["agent1"])["messages"]
report("3. agent1 takes over", f"ticket: taken_over_by {taken_over['taken_over_by']}, assigned {taken_over['assigned_agent_id']}, "
       f"last message {messages[-1]['sender_type']}: {messages[-1]['content']}", [
    ("taken_over_by is agent1", taken_over["taken_over_by"] == AGENT1_ID and taken_over["taken_over_at"] is not None),
    ("the ticket is assigned to agent1", taken_over["assigned_agent_id"] == AGENT1_ID),
    ("the last message is the 'joined' system message", messages[-1]["sender_type"] == "system" and messages[-1]["content"] == TAKEOVER_MESSAGE),
])

# Step 5: scenario 4, a customer may not take over; another agent gets a conflict
print("--- 4: customer1 and agent2 try to take over the same ticket")
customer_status = call_api_status("POST", f"/api/tickets/tickets/{policy_ticket_id}/takeover", tokens["customer1"])
agent2_status = call_api_status("POST", f"/api/tickets/tickets/{policy_ticket_id}/takeover", tokens["agent2"])
report("4. Takeover by others", f"customer1 -> {customer_status}, agent2 -> {agent2_status}", [
    (f"403 for customer1 (got {customer_status})", customer_status == 403),
    (f"409 for agent2 (got {agent2_status})", agent2_status == 409),
])

# Step 6: scenario 5, the customer writes on the taken-over ticket: the AI stays silent
question = "Can I also return the headphones?"
print("--- 5: customer1 writes on the taken-over ticket")
call_api("POST", f"/api/tickets/tickets/{policy_ticket_id}/messages", {"content": question}, tokens["customer1"])
reply = wait_for_ai_reply(tokens["customer1"], policy_ticket_id, 1, SILENCE_WAIT_SECONDS)
runs = wait_for_activity(tokens["agent1"], policy_ticket_id, 2)
newest_run = runs[-1] if runs else {}
report("5. The AI is silent while a human has the ticket", f"Q: {question}\nA: {reply or '(no AI message)'}\n"
       f"newest run: {newest_run.get('status')} / {newest_run.get('note')}", [
    (f"no AI message within {SILENCE_WAIT_SECONDS} seconds", reply == ""),
    ("the newest run is skipped with human_takeover", newest_run.get("status") == "skipped" and newest_run.get("note") == "human_takeover"),
])

# Step 7: scenario 6, only the agent who took over (or an admin) may release
print("--- 6: agent2 tries to release, then agent1 releases")
agent2_status = call_api_status("POST", f"/api/tickets/tickets/{policy_ticket_id}/release", tokens["agent2"])
released = call_api("POST", f"/api/tickets/tickets/{policy_ticket_id}/release", None, tokens["agent1"])
messages = call_api("GET", f"/api/tickets/tickets/{policy_ticket_id}", None, tokens["agent1"])["messages"]
report("6. Release", f"agent2 -> {agent2_status}; after agent1's release: taken_over_by {released['taken_over_by']}, "
       f"last message {messages[-1]['sender_type']}: {messages[-1]['content']}", [
    (f"403 for agent2 (got {agent2_status})", agent2_status == 403),
    ("taken_over_by is empty after agent1's release", released["taken_over_by"] is None and released["taken_over_at"] is None),
    ("the last message is the 'AI assistant is back' system message",
     messages[-1]["sender_type"] == "system" and messages[-1]["content"] == RELEASE_MESSAGE),
])

# Step 8: scenario 7, after the release the AI answers again
question = "Thanks! One more question: how long does standard delivery take?"
print("--- 7: customer1 writes after the release")
call_api("POST", f"/api/tickets/tickets/{policy_ticket_id}/messages", {"content": question}, tokens["customer1"])
reply = wait_for_ai_reply(tokens["customer1"], policy_ticket_id, 1, REPLY_WAIT_SECONDS)
report("7. The AI is back", f"Q: {question}\nA: {reply or '(no reply)'}", [
    (f"an AI reply arrives within {REPLY_WAIT_SECONDS} seconds", reply != ""),
])

# Step 9: scenario 8, an escalated ticket goes back to open when it is released
question = "I want to speak to a person, please."
print("--- 8: customer3 asks for a person; agent1 takes over and releases")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Speak to a person", "message": question}, tokens["customer3"])
escalated_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer3"], escalated_ticket_id, 0, REPLY_WAIT_SECONDS)
status_before = call_api("GET", f"/api/tickets/tickets/{escalated_ticket_id}", None, tokens["agent1"])["ticket"]["status"]
runs = wait_for_activity(tokens["agent1"], escalated_ticket_id, 1)
escalation_reason = runs[0]["escalation_reason"] if runs else None
call_api("POST", f"/api/tickets/tickets/{escalated_ticket_id}/takeover", None, tokens["agent1"])
released = call_api("POST", f"/api/tickets/tickets/{escalated_ticket_id}/release", None, tokens["agent1"])
report("8. Release reopens an escalated ticket (customer3)", f"Q: {question}\nA: {reply or '(no reply)'}\n"
       f"status before: {status_before}, escalation_reason: {escalation_reason}, status after release: {released['status']}", [
    (f"the ticket was escalated (got {status_before})", status_before == "escalated"),
    ("the run has an escalation_reason", bool(escalation_reason)),
    (f"after the release the status is open (got {released['status']})", released["status"] == "open"),
])

# Step 10: scenario 9, customers can never read the activity log
print("--- 9: customer1 asks for the activity log")
customer_status = call_api_status("GET", f"/api/agent/tickets/{policy_ticket_id}/activity", tokens["customer1"])
report("9. Activity log is staff only", f"customer1 -> {customer_status}", [
    (f"403 for customer1 (got {customer_status})", customer_status == 403),
])

# Step 11: the summary
print(f"Tickets: policy {policy_ticket_id}, order {order_ticket_id}, escalated {escalated_ticket_id}")
print(f"Summary: {sum(scenario_results)}/{len(scenario_results)} scenarios passed")
if not all(scenario_results):
    raise SystemExit(1)
