# The tools the model may call. Each one validates its input with Pydantic (args_schema), calls a service through
# one gRPC client function, and returns text for the model (compact JSON for the order tools). Tools never touch
# a database.
#
# Who the customer is never comes from the model: customer_id and ticket_id are read from the graph state by the
# ToolNode (InjectedState) and are left out of the schema the model sees. If the model sends them anyway, the
# ToolNode overwrites them with the values from the state. Ownership is enforced by the Order service.
#
# Expected business errors (no such order, not eligible, already requested) arrive as a ToolCallError from
# clients.py and are returned to the model by the graph's ToolNode; every other error propagates (see graph.py).
#
# M9: request_refund (tier 3) only creates an approval request in the Ticket service. No tool executes a refund,
# and the amount always comes from the Order service, never from the model.
import json
import logging
import uuid
from datetime import datetime
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, StringConstraints

from app.agent import clients
from app.errors import ToolCallError

logger = logging.getLogger(__name__)

SEARCH_TOP_K = 4

# Model-visible argument types, validated before any gRPC call
OrderNumber = Annotated[
    str, Field(pattern=r"^ORD-\d+$", description="The order number exactly as shown on the order, for example 'ORD-1001'.")
]
OrderItemId = Annotated[
    uuid.UUID, Field(description="The item_id of one item, taken from the get_order result for this order.")
]
# 3 to 500 visible characters (surrounding spaces are removed)
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]

# Injected by the ToolNode from the graph state; never part of the model's schema
InjectedCustomerId = Annotated[str, InjectedState("customer_id")]
InjectedTicketId = Annotated[str, InjectedState("ticket_id")]


class SearchKnowledgeBaseInput(BaseModel):
    # 1 to 500 visible characters (surrounding spaces are removed), the Knowledge service's limit
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="A short search query in plain words, for example 'return window for electronics'.",
    )


class ListOrdersInput(BaseModel):
    customer_id: InjectedCustomerId


class GetOrderInput(BaseModel):
    order_number: OrderNumber
    customer_id: InjectedCustomerId


class CheckReturnEligibilityInput(BaseModel):
    order_number: OrderNumber
    order_item_id: OrderItemId
    customer_id: InjectedCustomerId


class CreateReturnRequestInput(BaseModel):
    order_number: OrderNumber
    order_item_id: OrderItemId
    reason: Reason = Field(description="The customer's reason for the return, in a few words, for example 'Wrong size'.")
    customer_id: InjectedCustomerId


class RequestRefundInput(BaseModel):
    order_number: OrderNumber
    order_item_id: OrderItemId
    customer_id: InjectedCustomerId
    ticket_id: InjectedTicketId


class EscalateToHumanInput(BaseModel):
    reason: Reason = Field(
        description="A short note for the support staff (not shown to the customer) on why a human is needed."
    )
    ticket_id: InjectedTicketId


# M10: response_format="content_and_artifact": the model sees exactly the same numbered text as before; the raw
# results (document id, title, section, score) stay on the ToolMessage as its artifact for the AI activity log.
@tool(args_schema=SearchKnowledgeBaseInput, response_format="content_and_artifact")
def search_knowledge_base(query: str) -> tuple[str, list[dict]]:
    """Search the company's policy documents.

    Use this for ANY question about company policy: returns, refunds, shipping and delivery times,
    order cancellation, warranty, payment methods, order statuses, or support hours and how to contact support.
    Always search before you state any policy, number, time limit or rule. If the results do not answer
    the question, search again with different words (for example a synonym or the policy name) before
    concluding that the policy does not cover it.
    """
    # Step 1: the model filled in the query; Pydantic (args_schema) has already validated it.
    # Search through the Knowledge service.
    results = clients.search_knowledge(query, top_k=SEARCH_TOP_K)
    if not results:
        return "No relevant policy was found for this query.", []

    # Step 2: one numbered block per result: "[1] Title > Section", then the chunk text
    # (the stored chunk text starts with the same "Title > Section" line, so it is removed there)
    blocks = []
    for number, result in enumerate(results, start=1):
        heading = f"{result['title']} > {result['section']}"
        text = result["content"].removeprefix(heading).strip()
        blocks.append(f"[{number}] {heading}\n{text}")
    return "\n\n".join(blocks), results


@tool(args_schema=ListOrdersInput)
def list_orders(customer_id: str) -> str:
    """List all orders of the customer you are chatting with: order number, status, order date, total and item names.

    Use it when the customer asks what they ordered or which orders they have, or has not said which order they
    mean. For tracking, items, shipment details or returns, call get_order for the order afterwards.
    """
    # Step 1: customer_id was injected from the ticket; the Order service returns only this customer's orders
    orders = clients.list_orders(customer_id)

    # Step 2: compact JSON for the model (the order date without the time)
    order_rows = []
    for order in orders:
        order_rows.append({
            "order_number": order["order_number"],
            "status": order["status"],
            "ordered_on": datetime.fromisoformat(order["ordered_at"]).date().isoformat(),
            "total_amount": order["total_amount"],
            "item_names": order["item_names"],
        })
    return json.dumps({"orders": order_rows})


@tool(args_schema=GetOrderInput)
def get_order(order_number: str, customer_id: str) -> str:
    """Look up one of the customer's orders by its order number.

    Returns the order status, order date and total, every item (item_id, product_name, category, quantity,
    unit_price, and return_request: the item's return with its status "requested" or "refunded" and its
    refund_amount, or null if no return was started) and the shipment (carrier, tracking_number, status,
    shipped_on, estimated_delivery, delivered_on), or shipment null if the order has not shipped or was cancelled.
    Use it for "where is my order", tracking and delivery questions, and order details, and to find an item's
    item_id before check_return_eligibility, create_return_request or request_refund. The item_id is for tools
    only: never show it to the customer. If the order does not belong to this customer, it is reported as not found.
    """
    # Step 1: customer_id was injected from the ticket; another customer's order is "not found"
    order = clients.get_order(order_number, customer_id)

    # Step 2: compact JSON for the model (amounts are already strings with two decimals, dates ISO strings).
    # An item's return shows only its status and refund amount (the model needs no return request id).
    items = []
    for item in order["items"]:
        return_request = None
        if item["return_request"] is not None:
            return_request = {
                "status": item["return_request"]["status"],
                "refund_amount": item["return_request"]["refund_amount"],
            }
        items.append({
            "item_id": item["id"],
            "product_name": item["product_name"],
            "category": item["category"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
            "return_request": return_request,
        })
    return json.dumps({
        "order_number": order["order_number"],
        "status": order["status"],
        "ordered_on": datetime.fromisoformat(order["ordered_at"]).date().isoformat(),
        "total_amount": order["total_amount"],
        "items": items,
        "shipment": order["shipment"],
    })


@tool(args_schema=CheckReturnEligibilityInput)
def check_return_eligibility(order_number: str, order_item_id: uuid.UUID, customer_id: str) -> str:
    """Check whether one item of the customer's order can be returned. This does NOT start a return.

    The store's return rules are applied by the order system. Returns eligible (true or false), reason_code
    (eligible, not_delivered, non_returnable, window_expired or already_requested), reason (a sentence),
    refund_amount (only when eligible), window_days and days_remaining.
    Call get_order first to get the item_id. Explain the result to the customer in your own words.
    """
    # Step 1: customer_id was injected from the ticket; the Order service applies the rules
    result = clients.check_return_eligibility(order_number, str(order_item_id), customer_id)

    # Step 2: compact JSON for the model
    return json.dumps(result)


@tool(args_schema=CreateReturnRequestInput)
def create_return_request(order_number: str, order_item_id: uuid.UUID, reason: str, customer_id: str) -> str:
    """Start a return for one item of the customer's order.

    Only call this when the customer has clearly asked to return this specific item, in their latest message or
    as a yes to your question, and you know their reason. Check eligibility with check_return_eligibility first.
    The order system checks eligibility again and refuses a second return for the same item. Creating the return
    does not refund anything: right after it succeeds, call request_refund for the same item.
    """
    # Step 1: customer_id was injected from the ticket; the Order service re-checks eligibility and duplicates
    return_request = clients.create_return_request(order_number, str(order_item_id), reason, customer_id)

    # Step 2: compact JSON for the model
    return json.dumps({
        "return_request": {
            "return_request_id": return_request["id"],
            "order_number": return_request["order_number"],
            "item_id": return_request["order_item_id"],
            "status": return_request["status"],
            "refund_amount": return_request["refund_amount"],
            "reason": return_request["reason"],
            "created_at": return_request["created_at"],
        }
    })


# response_format="content_and_artifact": the model sees only the text; the artifact (with the approval id) stays
# on the ToolMessage, where graph.py's post_reply reads it to pause the run for a pending approval.
@tool(args_schema=RequestRefundInput, response_format="content_and_artifact")
def request_refund(
    order_number: str, order_item_id: uuid.UUID, customer_id: str, ticket_id: str
) -> tuple[str, dict | None]:
    """Request the refund for one item that has a return. This only creates a refund request; it never pays anything.

    Every return in this store is for a refund: call this right after create_return_request succeeds, for the same
    item, and also when the customer asks for a refund on an item whose return_request (from get_order) has status
    "requested". The refund amount is set by the order system. The result has status "approved" (approved now by
    the store's refund policy) or "pending" (the support team will review it), plus auto_approved, amount and summary.
    If the item has no return yet, you are told to create the return first.
    """
    # Step 1: find the item on the customer's order (customer_id was injected from the ticket)
    order = clients.get_order(order_number, customer_id)
    item = None
    for order_item in order["items"]:
        if order_item["id"] == str(order_item_id):
            item = order_item
    if item is None:
        raise ToolCallError("No item with that item_id was found on this order.")

    # Step 2: the refund belongs to a return request that is still open
    return_request = item["return_request"]
    if return_request is None:
        message = (
            "No return has been started for this item yet. "
            "Start it with create_return_request first, then call request_refund."
        )
        return message, None
    if return_request["status"] == "refunded":
        return "This item has already been refunded.", None

    # Step 3: create the approval in the Ticket service. The ticket is injected, the amount comes from the
    # Order service and the summary is written here; the model chooses only the item.
    summary = f"{item['product_name']} from {order['order_number']}"
    approval = clients.create_approval(ticket_id, "refund", return_request["id"], return_request["refund_amount"], summary)
    logger.info("Refund approval %s for ticket %s created as %s", approval["id"], ticket_id, approval["status"])

    # Step 4: compact JSON for the model; the approval id goes only into the artifact
    content = json.dumps({
        "status": approval["status"],
        "auto_approved": approval["auto_approved"],
        "amount": approval["amount"],
        "summary": approval["summary"],
    })
    return content, {"approval_id": approval["id"], "status": approval["status"]}


@tool(args_schema=EscalateToHumanInput)
def escalate_to_human(reason: str, ticket_id: str) -> str:
    """Hand this ticket over to a human support agent. You stop answering on this ticket after this.

    Use it when the customer asks for a human; when they need something your other tools cannot do (for example
    a replacement for an item that arrived damaged or wrong, a warranty claim, cancelling an order, a payment
    problem, or a delivery more than 10 days late); when the customer is clearly upset; or when you have tried
    twice and still cannot help. Do NOT use it when the customer asks to return or refund a faulty item: use the
    return and refund tools for that.
    The reason is a short note for the support staff and is never shown to the customer.
    """
    # Step 1: ticket_id was injected from the state; set the ticket to "escalated" in the Ticket service
    ticket = clients.escalate_ticket(ticket_id, reason)
    logger.info("Ticket %s escalated to a human: %s", ticket_id, reason)

    # Step 2: compact JSON for the model
    return json.dumps({
        "ticket_status": ticket["status"],
        "next_step": "A human support agent will take over this ticket during support hours "
        "(Monday to Saturday, 9:00 to 19:00 IST). Tell the customer.",
    })


# The list graph.py binds to the model and gives to the ToolNode
TOOLS = [
    search_knowledge_base,
    list_orders,
    get_order,
    check_return_eligibility,
    create_return_request,
    escalate_to_human,
    request_refund,
]

# Tier of every tool (CLAUDE.md section 9): 1 read-only, 2 workflow, 3 approval request.
# For documentation and for the M10 activity log (graph.run_agent adds the tier to every logged tool call).
TOOL_TIERS = {
    "search_knowledge_base": 1,
    "list_orders": 1,
    "get_order": 1,
    "check_return_eligibility": 1,
    "create_return_request": 2,
    "escalate_to_human": 2,
    "request_refund": 3,
}
