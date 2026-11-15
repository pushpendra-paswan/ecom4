# Checks the refund flow end to end (M9): the AI requests refunds, small ones are approved by policy, large ones
# pause the AI until a human (agent1) decides, and the Order service refunds only verified approvals.
# Run from the repo root on the host, with the stack up and seeded (identity users, knowledge documents, tickets)
# and FRESH demo orders:
#   docker compose exec order python seed.py --reset
#   python scripts/check_refund_flow.py
# Standard library only. Each scenario opens a ticket as the named customer through the Ticket REST API, waits for
# the AI reply, and checks the result against REAL data from the Ticket, Order and Agent REST APIs. It prints every
# step, the full AI replies, PASS/FAIL per check and a summary. Takes about three minutes.
# Keyword checks are rough on purpose: read the printed replies too. If scenarios fail, improve the prompt or the
# tool descriptions in the agent service, never these checks.
import json
import time
import urllib.request

BASE_URL = "http://localhost:8080"
DEMO_PASSWORD = "Password123!"
POLL_SECONDS = 2
# A refund conversation makes several tool calls, so the first reply can take a while
REPLY_WAIT_SECONDS = 90
# After a decision or an auto-approval, the outcome must show up within this time
OUTCOME_WAIT_SECONDS = 30
# The exact text the agent posts while a ticket is awaiting a refund decision (app/agent/prompts.py)
AWAITING_APPROVAL_MESSAGE = (
    "Your refund request is with our support team for review. I'll update you here as soon as there's a decision."
)
REJECTION_NOTE = "Photos show physical damage, which the return policy does not cover."

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


# The item's return request as the order's owner sees it on GET /orders/{number} (None if there is none)
def find_item_return(token: str, order_number: str, product_name: str) -> dict | None:
    order = call_api("GET", f"/api/orders/orders/{order_number}", None, token)
    for item in order["items"]:
        if item["product_name"] == product_name:
            return item["return_request"]
    return None


# The approval for a return request, read as staff (None if there is none)
def find_approval(staff_token: str, return_request_id: str) -> dict | None:
    for approval in call_api("GET", "/api/tickets/approvals", None, staff_token):
        if approval["return_request_id"] == return_request_id:
            return approval
    return None


# Prints one scenario with a PASS/FAIL line per check and remembers whether all checks passed
def report(name: str, question: str, reply: str, checks: list) -> None:
    print(f"=== {name}")
    print(f"Q: {question}")
    print(f"A: {reply or '(no reply)'}")
    for description, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {description}")
    scenario_results.append(all(passed for _, passed in checks))
    print()


print("Before running this, reset the demo orders (fresh dates, no returns or refunds):")
print("  docker compose exec order python seed.py --reset")
print()

# Step 1: log in as the three demo customers and as agent1 (staff: approvals, decisions and agent runs)
tokens = {}
for user in ["customer1", "customer2", "customer3", "agent1"]:
    login = {"email": f"{user}@example.com", "password": DEMO_PASSWORD}
    tokens[user] = call_api("POST", "/api/identity/login", login, None)["access_token"]

# Step 2: scenario 1, a small refund (headphones, 4500): approved by policy and refunded by the Order service
question = "The headphones from ORD-1001 stopped charging. I want to return them and get a refund."
print("--- 1: customer1 asks for a small refund")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Headphones refund", "message": question}, tokens["customer1"])
ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer1"], ticket_id, 0, REPLY_WAIT_SECONDS)
headphones_return = find_item_return(tokens["customer1"], "ORD-1001", "Wireless headphones")
deadline = time.time() + OUTCOME_WAIT_SECONDS
while headphones_return is not None and headphones_return["status"] != "refunded" and time.time() < deadline:
    time.sleep(POLL_SECONDS)
    headphones_return = find_item_return(tokens["customer1"], "ORD-1001", "Wireless headphones")
approval = find_approval(tokens["agent1"], headphones_return["id"]) if headphones_return else None
ticket = call_api("GET", f"/api/tickets/tickets/{ticket_id}", None, tokens["customer1"])["ticket"]
report("1. Small refund, auto-approved (customer1)", question, reply, [
    (f"the headphones' return is refunded (got {headphones_return['status'] if headphones_return else 'no return'})",
     headphones_return is not None and headphones_return["status"] == "refunded"),
    (f"the approval is approved with auto_approved=true (got {approval['status'] + ', ' + str(approval['auto_approved']) if approval else 'none'})",
     approval is not None and approval["status"] == "approved" and approval["auto_approved"]),
    (f"the ticket is not awaiting_approval (got {ticket['status']})", ticket["status"] != "awaiting_approval"),
    ("the reply says the refund is approved", "approved" in reply.lower()),
])

# Step 3: scenario 2, a large refund (smartwatch, 18000): pending, the ticket waits, the AI pauses
question = "The smartwatch from ORD-1004 won't turn on. Please return it and refund me."
print("--- 2: customer2 asks for a large refund")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Smartwatch refund", "message": question}, tokens["customer2"])
smartwatch_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer2"], smartwatch_ticket_id, 0, REPLY_WAIT_SECONDS)
smartwatch_return = find_item_return(tokens["customer2"], "ORD-1004", "Smartwatch")
smartwatch_approval = find_approval(tokens["agent1"], smartwatch_return["id"]) if smartwatch_return else None
ticket = call_api("GET", f"/api/tickets/tickets/{smartwatch_ticket_id}", None, tokens["customer2"])["ticket"]
report("2. Large refund, pending (customer2)", question, reply, [
    (f"the smartwatch approval is pending (got {smartwatch_approval['status'] if smartwatch_approval else 'none'})",
     smartwatch_approval is not None and smartwatch_approval["status"] == "pending"),
    (f"the ticket is awaiting_approval (got {ticket['status']})", ticket["status"] == "awaiting_approval"),
    ("the reply says it is under review", "review" in reply.lower()),
    (f"the return is still requested (got {smartwatch_return['status'] if smartwatch_return else 'no return'})",
     smartwatch_return is not None and smartwatch_return["status"] == "requested"),
])

# Step 4: scenario 3, the customer asks again while the refund waits: one fixed message, no model run
question = "Any update on my refund?"
print("--- 3: customer2 writes again while the ticket is awaiting approval")
message = call_api(
    "POST", f"/api/tickets/tickets/{smartwatch_ticket_id}/messages", {"content": question}, tokens["customer2"]
)
reply = wait_for_ai_reply(tokens["customer2"], smartwatch_ticket_id, 1, OUTCOME_WAIT_SECONDS)
runs = call_api("GET", f"/api/agent/runs?ticket_id={smartwatch_ticket_id}", None, tokens["agent1"])
follow_up_runs = [run for run in runs if run["trigger_message_id"] == message["id"]]
run_status = f"{follow_up_runs[0]['status']} / {follow_up_runs[0]['note']}" if follow_up_runs else "no run"
report("3. Message while awaiting approval (customer2, follow-up)", question, reply, [
    ("the reply is exactly the awaiting-approval message", reply == AWAITING_APPROVAL_MESSAGE),
    (f"the run is skipped with awaiting_approval (got {run_status})",
     len(follow_up_runs) == 1 and follow_up_runs[0]["status"] == "skipped" and follow_up_runs[0]["note"] == "awaiting_approval"),
])

# Step 5: scenario 4, agent1 approves: the paused run resumes and posts the outcome, the Order service refunds
print("--- 4: agent1 approves the smartwatch refund")
question = "(agent1 approves the smartwatch refund)"
reply = ""
ticket = {"status": "?"}
if smartwatch_approval is not None:
    call_api("POST", f"/api/tickets/approvals/{smartwatch_approval['id']}/approve", {}, tokens["agent1"])
    reply = wait_for_ai_reply(tokens["customer2"], smartwatch_ticket_id, 2, OUTCOME_WAIT_SECONDS)
    deadline = time.time() + OUTCOME_WAIT_SECONDS
    smartwatch_return = call_api("GET", f"/api/orders/returns/{smartwatch_return['id']}", None, tokens["customer2"])
    while smartwatch_return["status"] != "refunded" and time.time() < deadline:
        time.sleep(POLL_SECONDS)
        smartwatch_return = call_api("GET", f"/api/orders/returns/{smartwatch_return['id']}", None, tokens["customer2"])
    smartwatch_approval = call_api("GET", f"/api/tickets/approvals/{smartwatch_approval['id']}", None, tokens["agent1"])
    ticket = call_api("GET", f"/api/tickets/tickets/{smartwatch_ticket_id}", None, tokens["customer2"])["ticket"]
outcome_messages = [
    entry for entry in call_api("GET", f"/api/tickets/tickets/{smartwatch_ticket_id}", None, tokens["customer2"])["messages"]
    if entry["sender_type"] == "ai" and entry["content"].startswith("Good news!")
]
report("4. Human approves (agent1)", question, reply, [
    ("the AI message contains 'approved' and '18,000'", "approved" in reply and "18,000" in reply),
    (f"the return is refunded (got {smartwatch_return['status'] if smartwatch_return else 'no return'})",
     smartwatch_return is not None and smartwatch_return["status"] == "refunded"),
    (f"the approval is approved by a human (got {smartwatch_approval['status'] if smartwatch_approval else 'none'})",
     smartwatch_approval is not None and smartwatch_approval["status"] == "approved"
     and not smartwatch_approval["auto_approved"]),
    (f"the ticket is open (got {ticket['status']})", ticket["status"] == "open"),
    (f"exactly one outcome message (got {len(outcome_messages)})", len(outcome_messages) == 1),
])

# Step 6: scenario 5, another large refund (tablet, 24000) that will be rejected
question = "Please return the tablet from ORD-1011 and refund me, the screen flickers."
print("--- 5: customer3 asks for a large refund")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Tablet refund", "message": question}, tokens["customer3"])
tablet_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer3"], tablet_ticket_id, 0, REPLY_WAIT_SECONDS)
tablet_return = find_item_return(tokens["customer3"], "ORD-1011", "Tablet")
tablet_approval = find_approval(tokens["agent1"], tablet_return["id"]) if tablet_return else None
report("5. Large refund, pending (customer3)", question, reply, [
    (f"the tablet approval is pending (got {tablet_approval['status'] if tablet_approval else 'none'})",
     tablet_approval is not None and tablet_approval["status"] == "pending"),
])

# Step 7: scenario 6, agent1 rejects it with a note: the outcome names the note, nothing is refunded
print("--- 6: agent1 rejects the tablet refund")
question = f"(agent1 rejects the tablet refund: {REJECTION_NOTE})"
reply = ""
if tablet_approval is not None:
    call_api("POST", f"/api/tickets/approvals/{tablet_approval['id']}/reject", {"note": REJECTION_NOTE}, tokens["agent1"])
    reply = wait_for_ai_reply(tokens["customer3"], tablet_ticket_id, 1, OUTCOME_WAIT_SECONDS)
    # give the Order service time to (wrongly) act on the rejection before checking that it did not
    time.sleep(5)
    tablet_return = call_api("GET", f"/api/orders/returns/{tablet_return['id']}", None, tokens["customer3"])
report("6. Human rejects (agent1)", question, reply, [
    ("the AI message contains 'not approved'", "not approved" in reply),
    ("the AI message contains the rejection note", REJECTION_NOTE.rstrip(".") in reply),
    (f"the return stays requested, so no refund exists (got {tablet_return['status'] if tablet_return else 'no return'})",
     tablet_return is not None and tablet_return["status"] == "requested"),
])

# Step 8: scenario 7, a refund for an item whose return already exists (jacket, from the seed)
question = "I want a refund for the jacket from ORD-1007."
print("--- 7: customer2 asks for a refund on an existing return")
created = call_api("POST", "/api/tickets/tickets", {"subject": "Jacket refund", "message": question}, tokens["customer2"])
jacket_ticket_id = created["ticket"]["id"]
reply = wait_for_ai_reply(tokens["customer2"], jacket_ticket_id, 0, REPLY_WAIT_SECONDS)
jacket_return = find_item_return(tokens["customer2"], "ORD-1007", "Jacket")
jacket_approval = find_approval(tokens["agent1"], jacket_return["id"]) if jacket_return else None
expected_word = "approved"
if jacket_approval is not None and jacket_approval["status"] == "pending":
    expected_word = "review"
report("7. Refund for an existing return (customer2)", question, reply, [
    (f"an approval for the jacket exists (got {jacket_approval['status'] if jacket_approval else 'none'})",
     jacket_approval is not None),
    (f"the reply reflects its status ('{expected_word}')", expected_word in reply.lower()),
])

# Step 9: summary, plus the ids for the manual duplicate-event check
print(f"Smartwatch: ticket {smartwatch_ticket_id}, approval {smartwatch_approval['id'] if smartwatch_approval else '-'}")
print(f"Summary: {sum(scenario_results)}/{len(scenario_results)} scenarios passed")
if not all(scenario_results):
    raise SystemExit(1)
