# gRPC clients for the agent: the ONLY way the agent reaches other services (tools never touch a database).
# Every entry point returns plain Python dicts or lists, never proto objects.
# Errors: NOT_FOUND, INVALID_ARGUMENT and ALREADY_EXISTS are expected business answers ("no such order", "not
# eligible", "already requested"); they become a ToolCallError that the graph hands back to the model.
# Every other gRPC error (service down, deadline exceeded, internal error) propagates unchanged to the Celery
# task, which retries.
import time

import grpc
import jwt

from app.config import settings
from app.errors import ToolCallError
from app.generated import knowledge_pb2, knowledge_pb2_grpc, order_pb2, order_pb2_grpc, ticket_pb2, ticket_pb2_grpc

# Deadlines (seconds), the M5 convention: 5 s by default, 15 s for search (it makes an embedding call)
DEFAULT_TIMEOUT_SECONDS = 5
SEARCH_TIMEOUT_SECONDS = 15
SERVICE_TOKEN_LIFETIME_SECONDS = 60

# The gRPC status codes that mean "the service answered no", not "the call failed"
TOOL_ERROR_CODES = (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.ALREADY_EXISTS)

# One channel per target per process, created on first use and then reused (channels are thread-safe).
# Not created at import time: Celery imports this module BEFORE it forks its worker processes,
# and a gRPC channel must not be shared across a fork.
ticket_stub = None
knowledge_stub = None
order_stub = None


# A fresh service token for every call: HS256, signed with the shared secret, sub=agent, valid for 60 seconds
def service_token_metadata() -> list:
    issued_at = int(time.time())
    claims = {"sub": "agent", "iat": issued_at, "exp": issued_at + SERVICE_TOKEN_LIFETIME_SECONDS}
    token = jwt.encode(claims, settings.service_token_secret, algorithm="HS256")
    return [("authorization", f"Bearer {token}")]


# Called in the except block of every client function. Raises a ToolCallError for the three business error codes;
# for any other code it returns, and the caller re-raises the original grpc.RpcError unchanged.
def raise_tool_error(error: grpc.RpcError) -> None:
    # Step 1: the Order service says only "Order not found"; give the model a clearer sentence to explain
    if error.code() == grpc.StatusCode.NOT_FOUND and error.details() == "Order not found":
        raise ToolCallError("No order with that number was found for this customer.") from error

    # Step 2: the other business errors keep the server's own message
    if error.code() in TOOL_ERROR_CODES:
        raise ToolCallError(error.details()) from error


# The ticket with its messages, oldest first
def get_ticket(ticket_id: str) -> dict:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: call the Ticket service
    try:
        ticket = ticket_stub.GetTicket(
            ticket_pb2.GetTicketRequest(ticket_id=ticket_id),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    messages = []
    for message in ticket.messages:
        messages.append({
            "id": message.id,
            "sender_type": message.sender_type,
            "content": message.content,
            "created_at": message.created_at,
        })
    return {
        "id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "status": ticket.status,
        "messages": messages,
    }


# Posts a message as "ai" or "system" and returns it
def post_message(ticket_id: str, sender_type: str, content: str) -> dict:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: call the Ticket service
    try:
        message = ticket_stub.PostMessage(
            ticket_pb2.PostMessageRequest(ticket_id=ticket_id, sender_type=sender_type, content=content),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    return {
        "id": message.id,
        "sender_type": message.sender_type,
        "content": message.content,
        "created_at": message.created_at,
    }


# Sets the ticket to "escalated" so a human takes over; returns the ticket's id and new status
def escalate_ticket(ticket_id: str, reason: str) -> dict:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: call the Ticket service
    try:
        ticket = ticket_stub.EscalateTicket(
            ticket_pb2.EscalateTicketRequest(ticket_id=ticket_id, reason=reason),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    return {"id": ticket.id, "status": ticket.status}


# Semantic search over the policy documents, best match first
def search_knowledge(query: str, top_k: int) -> list[dict]:
    global knowledge_stub

    # Step 1: open the channel once per process
    if knowledge_stub is None:
        knowledge_stub = knowledge_pb2_grpc.KnowledgeServiceStub(
            grpc.insecure_channel(settings.knowledge_grpc_address)
        )

    # Step 2: call the Knowledge service
    try:
        response = knowledge_stub.Search(
            knowledge_pb2.SearchRequest(query=query, top_k=top_k),
            timeout=SEARCH_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    results = []
    for result in response.results:
        results.append({
            "title": result.title,
            "section": result.section,
            "content": result.content,
            "score": result.score,
        })
    return results


# The customer's orders (by order number), each with its item names
def list_orders(customer_id: str) -> list[dict]:
    global order_stub

    # Step 1: open the channel once per process
    if order_stub is None:
        order_stub = order_pb2_grpc.OrderServiceStub(grpc.insecure_channel(settings.order_grpc_address))

    # Step 2: call the Order service; it only returns this customer's orders
    try:
        response = order_stub.ListOrders(
            order_pb2.ListOrdersRequest(customer_id=customer_id),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    orders = []
    for order in response.orders:
        orders.append({
            "order_number": order.order_number,
            "status": order.status,
            "total_amount": order.total_amount,
            "ordered_at": order.ordered_at,
            "item_names": [item.product_name for item in order.items],
        })
    return orders


# One of the customer's orders with its items and shipment (shipment is None for placed and cancelled orders)
def get_order(order_number: str, customer_id: str) -> dict:
    global order_stub

    # Step 1: open the channel once per process
    if order_stub is None:
        order_stub = order_pb2_grpc.OrderServiceStub(grpc.insecure_channel(settings.order_grpc_address))

    # Step 2: call the Order service; another customer's order is NOT_FOUND
    try:
        order = order_stub.GetOrder(
            order_pb2.GetOrderRequest(order_number=order_number, customer_id=customer_id),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert the items
    items = []
    for item in order.items:
        items.append({
            "id": item.id,
            "product_name": item.product_name,
            "category": item.category,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
        })

    # Step 4: convert the shipment, if there is one
    shipment = None
    if order.HasField("shipment"):
        shipment = {
            "carrier": order.shipment.carrier,
            "tracking_number": order.shipment.tracking_number,
            "status": order.shipment.status,
            "shipped_on": order.shipment.shipped_on,
            "estimated_delivery": order.shipment.estimated_delivery,
            "delivered_on": order.shipment.delivered_on if order.shipment.HasField("delivered_on") else None,
        }

    return {
        "order_number": order.order_number,
        "status": order.status,
        "total_amount": order.total_amount,
        "ordered_at": order.ordered_at,
        "items": items,
        "shipment": shipment,
    }


# Can this item be returned? Not eligible is a normal answer (eligible False + reason), not an error.
def check_return_eligibility(order_number: str, order_item_id: str, customer_id: str) -> dict:
    global order_stub

    # Step 1: open the channel once per process
    if order_stub is None:
        order_stub = order_pb2_grpc.OrderServiceStub(grpc.insecure_channel(settings.order_grpc_address))

    # Step 2: call the Order service
    try:
        result = order_stub.CheckReturnEligibility(
            order_pb2.CheckReturnEligibilityRequest(
                order_number=order_number, order_item_id=order_item_id, customer_id=customer_id
            ),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python, with None where no value applies (like the REST result;
    # over gRPC window_days is 0 when no window applies)
    return {
        "eligible": result.eligible,
        "reason_code": result.reason_code,
        "reason": result.reason,
        "refund_amount": result.refund_amount if result.HasField("refund_amount") else None,
        "window_days": result.window_days if result.window_days > 0 else None,
        "days_remaining": result.days_remaining if result.HasField("days_remaining") else None,
    }


# Starts a return for one item. The Order service checks eligibility again and rejects a second request.
def create_return_request(order_number: str, order_item_id: str, reason: str, customer_id: str) -> dict:
    global order_stub

    # Step 1: open the channel once per process
    if order_stub is None:
        order_stub = order_pb2_grpc.OrderServiceStub(grpc.insecure_channel(settings.order_grpc_address))

    # Step 2: call the Order service
    try:
        return_request = order_stub.CreateReturnRequest(
            order_pb2.CreateReturnRequestRequest(
                order_number=order_number, order_item_id=order_item_id, reason=reason, customer_id=customer_id
            ),
            timeout=DEFAULT_TIMEOUT_SECONDS,
            metadata=service_token_metadata(),
        )
    except grpc.RpcError as error:
        raise_tool_error(error)
        raise

    # Step 3: convert to plain Python
    return {
        "id": return_request.id,
        "order_number": return_request.order_number,
        "order_item_id": return_request.order_item_id,
        "status": return_request.status,
        "refund_amount": return_request.refund_amount,
        "reason": return_request.reason,
        "created_at": return_request.created_at,
    }
