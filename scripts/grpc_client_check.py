# Checks the gRPC servers of the order, knowledge and ticket services against the seeded demo data.
# Run from the repo root on the host, with the stack up and seeded:
#   python scripts/grpc_client_check.py
# Needs grpcio, protobuf and PyJWT (see README); uses the generated stubs in scripts/generated/.
# Every call sends a fresh service token signed as "agent" with SERVICE_TOKEN_SECRET from the repo's .env.
# Prints one PASS/FAIL line per step and a summary. Safe to run again: it creates two new tickets and new approvals each time.
# M9: the 4500.00 approval is auto-approved for a made-up return request, so the order worker logs a verification
# failure for it ("return request ... does not exist"); that is expected.
import json
import pathlib
import time
import urllib.error
import urllib.request
import uuid

import grpc
import jwt

from generated import knowledge_pb2, knowledge_pb2_grpc, order_pb2, order_pb2_grpc, ticket_pb2, ticket_pb2_grpc

BASE_URL = "http://localhost:8080"
ORDER_ADDRESS = "localhost:50051"
KNOWLEDGE_ADDRESS = "localhost:50052"
TICKET_ADDRESS = "localhost:50053"

# Deadlines (seconds): every call has one. Search is longer because it makes an embedding call to OpenAI.
DEFAULT_TIMEOUT = 5
SEARCH_TIMEOUT = 15

# Fixed ids and login from the identity seed
CUSTOMER1_ID = "00000000-0000-0000-0000-000000000101"
CUSTOMER3_ID = "00000000-0000-0000-0000-000000000103"
CUSTOMER1_EMAIL = "customer1@example.com"
DEMO_PASSWORD = "Password123!"

# Service tokens: read the shared secret from the repo's .env (the same value the services get)
ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"
SERVICE_TOKEN_SECRET = ""
for line in ENV_FILE.read_text().splitlines():
    if line.startswith("SERVICE_TOKEN_SECRET="):
        SERVICE_TOKEN_SECRET = line.removeprefix("SERVICE_TOKEN_SECRET=").strip()
TOKEN_LIFETIME_SECONDS = 60

step_results = []


# Prints one result line and remembers it for the summary
def report(step: str, passed: bool, detail: str) -> None:
    step_results.append(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {step}  [{detail}]")


# A fresh service token for every call, signed as the agent service, valid for 60 seconds
def agent_metadata() -> list:
    issued_at = int(time.time())
    claims = {"sub": "agent", "iat": issued_at, "exp": issued_at + TOKEN_LIFETIME_SECONDS}
    token = jwt.encode(claims, SERVICE_TOKEN_SECRET, algorithm="HS256")
    return [("authorization", f"Bearer {token}")]


# Step 1: open one channel per service and create the stubs
order_stub = order_pb2_grpc.OrderServiceStub(grpc.insecure_channel(ORDER_ADDRESS))
knowledge_stub = knowledge_pb2_grpc.KnowledgeServiceStub(grpc.insecure_channel(KNOWLEDGE_ADDRESS))
ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(TICKET_ADDRESS))

print("--- Order service ---")

# Step 2: customer1 has exactly ORD-1001, ORD-1002 and ORD-1003
step = "ListOrders as customer1 -> ORD-1001, ORD-1002, ORD-1003"
try:
    response = order_stub.ListOrders(
        order_pb2.ListOrdersRequest(customer_id=CUSTOMER1_ID), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata()
    )
    order_numbers = [order.order_number for order in response.orders]
    report(step, order_numbers == ["ORD-1001", "ORD-1002", "ORD-1003"], f"got {order_numbers}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 3: customer1 can open ORD-1001; remember the headphones' item id for the eligibility check
step = "GetOrder ORD-1001 as customer1 -> the order with its items"
headphones_item_id = ""
try:
    order = order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1001", customer_id=CUSTOMER1_ID),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    for item in order.items:
        if item.product_name == "Wireless headphones":
            headphones_item_id = item.id
    report(step, order.order_number == "ORD-1001" and headphones_item_id != "",
           f"{order.order_number}, {len(order.items)} items, total {order.total_amount}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 4: ORD-1004 belongs to customer2, so for customer1 it does not exist
step = "GetOrder ORD-1004 as customer1 -> NOT_FOUND"
try:
    order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1004", customer_id=CUSTOMER1_ID),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.NOT_FOUND, error.code().name)

# Step 5: the headphones (delivered 5 days ago, 15-day electronics window) are eligible for 4500.00
step = "CheckReturnEligibility ORD-1001 headphones -> eligible, refund 4500.00"
try:
    result = order_stub.CheckReturnEligibility(
        order_pb2.CheckReturnEligibilityRequest(
            order_number="ORD-1001", order_item_id=headphones_item_id, customer_id=CUSTOMER1_ID
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, result.eligible and result.refund_amount == "4500.00",
           f"{result.reason_code}, refund {result.refund_amount}, {result.days_remaining} of {result.window_days} days left")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 6: the ORD-1002 T-shirt was delivered 40 days ago (30-day window); the item id comes from GetOrder
step = "CheckReturnEligibility ORD-1002 T-shirt -> not eligible, window_expired"
try:
    order = order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1002", customer_id=CUSTOMER1_ID),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    tshirt_item_id = ""
    for item in order.items:
        if item.product_name == "T-shirt":
            tshirt_item_id = item.id
    result = order_stub.CheckReturnEligibility(
        order_pb2.CheckReturnEligibilityRequest(
            order_number="ORD-1002", order_item_id=tshirt_item_id, customer_id=CUSTOMER1_ID
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, not result.eligible and result.reason_code == "window_expired",
           f"{result.reason_code}: {result.reason}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 7: customer3 returns the ORD-1008 socks. The first run creates the request; later runs get ALREADY_EXISTS.
step = "CreateReturnRequest ORD-1008 socks as customer3 -> created (first run) or ALREADY_EXISTS (later runs)"
try:
    order = order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1008", customer_id=CUSTOMER3_ID),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    socks_item_id = ""
    for item in order.items:
        if item.product_name == "Socks":
            socks_item_id = item.id
    return_request = order_stub.CreateReturnRequest(
        order_pb2.CreateReturnRequestRequest(
            order_number="ORD-1008", order_item_id=socks_item_id, reason="Wrong size", customer_id=CUSTOMER3_ID
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, return_request.status == "requested",
           f"created {return_request.id}, refund {return_request.refund_amount} (first run)")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.ALREADY_EXISTS, f"{error.code().name}: {error.details()}")

# Step 8: M9, the socks now have a return request, and GetOrder shows it on the item
step = "GetOrder ORD-1008 as customer3 -> the socks item shows its return_request"
try:
    order = order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1008", customer_id=CUSTOMER3_ID),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    socks_return = None
    for item in order.items:
        if item.product_name == "Socks" and item.HasField("return_request"):
            socks_return = item.return_request
    report(step, socks_return is not None and socks_return.refund_amount == "400.00",
           f"return {socks_return.id}, {socks_return.status}, {socks_return.refund_amount}" if socks_return else "no return_request")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

print("--- Knowledge service ---")

# Step 9: a gift-card question should find the returns policy first
step = "Search 'Can I return a gift card?' top_k=3 -> results, top one from Returns Policy"
try:
    response = knowledge_stub.Search(
        knowledge_pb2.SearchRequest(query="Can I return a gift card?", top_k=3),
        timeout=SEARCH_TIMEOUT,
        metadata=agent_metadata(),
    )
    top_title = response.results[0].title if response.results else "(no results)"
    top_section = response.results[0].section if response.results else ""
    report(step, len(response.results) > 0 and top_title == "Returns Policy",
           f"{len(response.results)} results, top: {top_title} / {top_section}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

print("--- Ticket service ---")

# Step 10: log in as customer1 and create a new ticket through the REST API (through Nginx)
step = "REST: log in as customer1 and create a ticket -> 201"
ticket_id = ""
try:
    login_body = json.dumps({"email": CUSTOMER1_EMAIL, "password": DEMO_PASSWORD}).encode()
    login_request = urllib.request.Request(
        f"{BASE_URL}/api/identity/login", data=login_body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(login_request, timeout=DEFAULT_TIMEOUT) as response:
        access_token = json.load(response)["access_token"]
    ticket_body = json.dumps({"subject": "gRPC check", "message": "Hello, this ticket was created by grpc_client_check.py"}).encode()
    ticket_request = urllib.request.Request(
        f"{BASE_URL}/api/tickets/tickets",
        data=ticket_body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(ticket_request, timeout=DEFAULT_TIMEOUT) as response:
        ticket_id = json.load(response)["ticket"]["id"]
    report(step, True, f"ticket {ticket_id}")
except (urllib.error.URLError, KeyError) as error:
    report(step, False, str(error))

# Step 11: the new ticket has exactly one message (the customer's first message)
step = "GetTicket -> the new ticket with 1 message"
try:
    ticket = ticket_stub.GetTicket(ticket_pb2.GetTicketRequest(ticket_id=ticket_id), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata())
    report(step, ticket.id == ticket_id and len(ticket.messages) == 1,
           f"status {ticket.status}, {len(ticket.messages)} message(s), first from {ticket.messages[0].sender_type if ticket.messages else '-'}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 12: the AI can post a message
step = "PostMessage as ai -> saved with sender_type ai and no sender_id"
try:
    message = ticket_stub.PostMessage(
        ticket_pb2.PostMessageRequest(ticket_id=ticket_id, sender_type="ai", content="Hi! I am the AI assistant."),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, message.sender_type == "ai" and not message.HasField("sender_id"), f"message {message.id}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 13: the conversation history now has two messages
step = "GetTicket again -> 2 messages (customer, ai)"
try:
    ticket = ticket_stub.GetTicket(ticket_pb2.GetTicketRequest(ticket_id=ticket_id), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata())
    sender_types = [message.sender_type for message in ticket.messages]
    report(step, sender_types == ["customer", "ai"], f"got {sender_types}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 14: gRPC callers may not post as a customer
step = "PostMessage as customer -> INVALID_ARGUMENT"
try:
    ticket_stub.PostMessage(
        ticket_pb2.PostMessageRequest(ticket_id=ticket_id, sender_type="customer", content="Pretending to be the customer"),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.INVALID_ARGUMENT, f"{error.code().name}: {error.details()}")

# Step 15: 4500.00 is at or below the auto-approve limit (5000)
step = "CreateApproval 4500.00 -> approved, auto_approved=true"
small_return_request_id = str(uuid.uuid4())
small_approval_id = ""
try:
    approval = ticket_stub.CreateApproval(
        ticket_pb2.CreateApprovalRequest(
            ticket_id=ticket_id,
            action_type="refund",
            return_request_id=small_return_request_id,
            amount="4500.00",
            summary="gRPC check: small refund",
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    small_approval_id = approval.id
    report(step, approval.status == "approved" and approval.auto_approved and approval.HasField("decided_at"),
           f"approval {approval.id}: {approval.status}, auto_approved={approval.auto_approved}, decided_at={approval.decided_at}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 16: M9, GetApproval returns the approval just created (an auto-approval has no decided_by and no note)
step = "GetApproval for the 4500.00 approval -> same approval, approved, no decided_by"
try:
    approval = ticket_stub.GetApproval(
        ticket_pb2.GetApprovalRequest(approval_id=small_approval_id), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata()
    )
    report(step, approval.id == small_approval_id and approval.status == "approved" and approval.amount == "4500.00"
           and approval.return_request_id == small_return_request_id and not approval.HasField("decided_by"),
           f"approval {approval.id}: {approval.status}, {approval.amount}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 17: an unknown approval id
step = "GetApproval for an unknown id -> NOT_FOUND"
try:
    ticket_stub.GetApproval(
        ticket_pb2.GetApprovalRequest(approval_id=str(uuid.uuid4())), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata()
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.NOT_FOUND, f"{error.code().name}: {error.details()}")

# Step 18: 18000.00 is above the limit, so it waits for a human (and the ticket becomes awaiting_approval)
step = "CreateApproval 18000.00 -> pending"
try:
    approval = ticket_stub.CreateApproval(
        ticket_pb2.CreateApprovalRequest(
            ticket_id=ticket_id,
            action_type="refund",
            return_request_id=str(uuid.uuid4()),
            amount="18000.00",
            summary="gRPC check: large refund",
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, approval.status == "pending" and not approval.auto_approved,
           f"approval {approval.id}: {approval.status}, auto_approved={approval.auto_approved}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 19: only one approval per return request
step = "CreateApproval again for the first return request -> ALREADY_EXISTS"
try:
    ticket_stub.CreateApproval(
        ticket_pb2.CreateApprovalRequest(
            ticket_id=ticket_id,
            action_type="refund",
            return_request_id=small_return_request_id,
            amount="4500.00",
            summary="gRPC check: duplicate",
        ),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.ALREADY_EXISTS, f"{error.code().name}: {error.details()}")

# Step 20: a fresh ticket (created over REST like in step 10) can be escalated to a human
step = "EscalateTicket on a fresh ticket -> status escalated"
try:
    ticket_body = json.dumps({"subject": "gRPC check: escalation", "message": "Please let a human look at this (grpc_client_check.py)"}).encode()
    ticket_request = urllib.request.Request(
        f"{BASE_URL}/api/tickets/tickets",
        data=ticket_body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(ticket_request, timeout=DEFAULT_TIMEOUT) as response:
        escalation_ticket_id = json.load(response)["ticket"]["id"]
    ticket = ticket_stub.EscalateTicket(
        ticket_pb2.EscalateTicketRequest(ticket_id=escalation_ticket_id, reason="grpc_client_check: customer asked for a human"),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, ticket.id == escalation_ticket_id and ticket.status == "escalated", f"ticket {ticket.id}: {ticket.status}")
except (urllib.error.URLError, KeyError, NameError) as error:
    report(step, False, str(error))
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

print("--- Deadlines and validation ---")

# Step 21: a 1 millisecond deadline is too short for any real call
step = "ListOrders with a 1 ms deadline -> DEADLINE_EXCEEDED"
try:
    order_stub.ListOrders(
        order_pb2.ListOrdersRequest(customer_id=CUSTOMER1_ID), timeout=0.001, metadata=agent_metadata()
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.DEADLINE_EXCEEDED, error.code().name)

# Step 22: customer_id is required
step = "GetOrder with an empty customer_id -> INVALID_ARGUMENT"
try:
    order_stub.GetOrder(order_pb2.GetOrderRequest(order_number="ORD-1001", customer_id=""), timeout=DEFAULT_TIMEOUT, metadata=agent_metadata())
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.INVALID_ARGUMENT, f"{error.code().name}: {error.details()}")

# Step 23: customer_id must be a UUID
step = "GetOrder with a malformed customer_id -> INVALID_ARGUMENT"
try:
    order_stub.GetOrder(
        order_pb2.GetOrderRequest(order_number="ORD-1001", customer_id="not-a-uuid"),
        timeout=DEFAULT_TIMEOUT,
        metadata=agent_metadata(),
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.INVALID_ARGUMENT, f"{error.code().name}: {error.details()}")

print("--- Service tokens ---")
list_orders_request = order_pb2.ListOrdersRequest(customer_id=CUSTOMER1_ID)

# Step 24: no token at all
step = "ListOrders without a token -> UNAUTHENTICATED"
try:
    order_stub.ListOrders(list_orders_request, timeout=DEFAULT_TIMEOUT)
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.UNAUTHENTICATED, f"{error.code().name}: {error.details()}")

# Step 25: something that is not a JWT
step = "ListOrders with a garbage token -> UNAUTHENTICATED"
try:
    order_stub.ListOrders(
        list_orders_request, timeout=DEFAULT_TIMEOUT, metadata=[("authorization", "Bearer not-a-real-token")]
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.UNAUTHENTICATED, f"{error.code().name}: {error.details()}")

# Step 26: a well-formed token signed with another secret
step = "ListOrders with a token signed with the wrong secret -> UNAUTHENTICATED"
issued_at = int(time.time())
wrong_secret_token = jwt.encode(
    {"sub": "agent", "iat": issued_at, "exp": issued_at + TOKEN_LIFETIME_SECONDS},
    "this-is-not-the-shared-secret-at-all",
    algorithm="HS256",
)
try:
    order_stub.ListOrders(
        list_orders_request, timeout=DEFAULT_TIMEOUT, metadata=[("authorization", f"Bearer {wrong_secret_token}")]
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.UNAUTHENTICATED, f"{error.code().name}: {error.details()}")

# Step 27: a correctly signed token that expired a minute ago
step = "ListOrders with an expired token -> UNAUTHENTICATED"
issued_at = int(time.time()) - 120
expired_token = jwt.encode(
    {"sub": "agent", "iat": issued_at, "exp": issued_at + TOKEN_LIFETIME_SECONDS},
    SERVICE_TOKEN_SECRET,
    algorithm="HS256",
)
try:
    order_stub.ListOrders(
        list_orders_request, timeout=DEFAULT_TIMEOUT, metadata=[("authorization", f"Bearer {expired_token}")]
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.UNAUTHENTICATED, f"{error.code().name}: {error.details()}")

# Step 28: a valid token, but the order service is not on the order server's allowlist
step = "ListOrders with a valid token for sub=order -> PERMISSION_DENIED"
issued_at = int(time.time())
order_token = jwt.encode(
    {"sub": "order", "iat": issued_at, "exp": issued_at + TOKEN_LIFETIME_SECONDS},
    SERVICE_TOKEN_SECRET,
    algorithm="HS256",
)
try:
    order_stub.ListOrders(
        list_orders_request, timeout=DEFAULT_TIMEOUT, metadata=[("authorization", f"Bearer {order_token}")]
    )
    report(step, False, "the call succeeded")
except grpc.RpcError as error:
    report(step, error.code() == grpc.StatusCode.PERMISSION_DENIED, f"{error.code().name}: {error.details()}")

# Step 29: M9, the order service is on the TICKET server's allowlist, so its token may call GetApproval
step = "GetApproval with a valid token for sub=order -> allowed"
issued_at = int(time.time())
order_token = jwt.encode(
    {"sub": "order", "iat": issued_at, "exp": issued_at + TOKEN_LIFETIME_SECONDS},
    SERVICE_TOKEN_SECRET,
    algorithm="HS256",
)
try:
    approval = ticket_stub.GetApproval(
        ticket_pb2.GetApprovalRequest(approval_id=small_approval_id),
        timeout=DEFAULT_TIMEOUT,
        metadata=[("authorization", f"Bearer {order_token}")],
    )
    report(step, approval.id == small_approval_id, f"approval {approval.id}: {approval.status}")
except grpc.RpcError as error:
    report(step, False, f"{error.code().name}: {error.details()}")

# Step 30: print the summary (and the ticket id, for the database check of the two approvals)
print()
print(f"Ticket created by this run: {ticket_id}")
print(f"Summary: {sum(step_results)}/{len(step_results)} steps passed")
if not all(step_results):
    raise SystemExit(1)
