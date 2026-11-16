# The AI agent: a LangGraph StateGraph, checkpointed in Postgres with thread_id = ticket_id.
#
#   START -> load_context -> agent -> (tool calls?) -> tools -> agent -> ... -> post_reply
#   post_reply -> END                                                    (no pending refund approval)
#   post_reply -> wait_for_approval -> (paused) ... -> announce_decision -> END   (M9: a refund waits for a human)
#
# Human in the loop (M9): when request_refund returned "pending", the run posts its reply and then pauses in
# wait_for_approval with interrupt(). The paused state is saved by the Postgres checkpointer, so it survives restarts.
# When a human decides, resume_agent() resumes the thread with Command(resume=...) and announce_decision posts a
# fixed outcome message (no model call).
#
# Entry points (called by run_service): run_agent(ticket_id) and resume_agent(ticket_id, approval_id).
# The node functions below are graph entry points.
#
# M10: if a human takes over the ticket while a run is working, the Ticket service rejects the run's "ai" message.
# post_reply and announce_decision then keep the text in reply_text, set reply_discarded and let the run finish
# normally. run_agent also returns the AI activity log for the run (tool calls, retrieved chunks, tokens).
import logging
from decimal import Decimal
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.agent import clients
from app.agent.prompts import REFUND_APPROVED_MESSAGE, REFUND_REJECTED_MESSAGE, SYSTEM_PROMPT
from app.agent.tools import TOOL_TIERS, TOOLS
from app.config import settings
from app.errors import ToolCallError

logger = logging.getLogger(__name__)

# Posted when the model returns an empty answer, after the final failed retry, and when the step limit is hit
FALLBACK_MESSAGE = "Sorry, I'm having trouble answering right now. A support agent will follow up with you."
MODEL_TIMEOUT_SECONDS = 30
MODEL_MAX_RETRIES = 2
# The Ticket service accepts messages of at most 5000 characters
MAX_REPLY_LENGTH = 5000
CHECKPOINT_POOL_MAX_SIZE = 2
# M10: tool results in the activity log are cut to this many characters
MAX_LOGGED_RESULT_LENGTH = 2000


class AgentState(TypedDict):
    ticket_id: str
    # The ticket's customer, set by load_context from the Ticket service. The order tools get it injected
    # (InjectedState); the model never sees or chooses it.
    customer_id: str
    ticket_subject: str
    # add_messages appends new messages (and handles RemoveMessage); the system prompt is never stored here
    messages: Annotated[list[AnyMessage], add_messages]
    context_until: str  # created_at (ISO string) of the newest ticket message this run read
    reply_message_id: str | None
    tool_call_count: int
    # The reason the model gave when it called escalate_to_human in this run (None if it did not)
    escalation_reason: str | None
    # M9: the approval this thread is paused for (a request_refund in this run returned "pending"); None otherwise
    pending_approval_id: str | None
    # M9: the value the thread was resumed with: {"approval_id": ...}
    approval_decision: dict | None
    # M10: how many ticket messages load_context put into "messages"; everything after them was added by this run
    history_count: int
    # M10: the text post_reply or announce_decision posted (or tried to post), and whether the Ticket service
    # rejected it because a human had taken over the ticket in the meantime
    reply_text: str | None
    reply_discarded: bool


# Built on first use, once per process (see run_agent). Never at import time: Celery imports this module
# before forking its worker processes, and database connections must not be shared across a fork.
model_with_tools = None
compiled_graph = None


# Node "load_context": the Ticket service is the source of truth for the conversation (human agents post there too),
# so every run throws away the messages in the checkpointed state and reloads the latest ones from the ticket.
def load_context(state: AgentState) -> dict:
    # Step 1: get the ticket with its messages (oldest first)
    ticket = clients.get_ticket(state["ticket_id"])
    history = ticket["messages"][-settings.agent_history_limit:]

    # Step 2: clear the old state messages, then add the ticket messages as LangChain messages
    new_messages = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
    for message in history:
        if message["sender_type"] == "customer":
            new_messages.append(HumanMessage(content=message["content"], id=message["id"]))
        elif message["sender_type"] == "ai":
            new_messages.append(AIMessage(content=message["content"], id=message["id"]))
        elif message["sender_type"] == "agent":
            content = f"(Reply from a human support agent) {message['content']}"
            new_messages.append(AIMessage(content=content, id=message["id"]))
        else:
            content = f"(System notice) {message['content']}"
            new_messages.append(AIMessage(content=content, id=message["id"]))

    # Step 3: the customer comes from the ticket (never from the model), then reset the per-run fields
    return {
        "customer_id": ticket["customer_id"],
        "ticket_subject": ticket["subject"],
        "messages": new_messages,
        "context_until": history[-1]["created_at"],
        "reply_message_id": None,
        "tool_call_count": 0,
        "escalation_reason": None,
        "pending_approval_id": None,
        "approval_decision": None,
        "history_count": len(history),
        "reply_text": None,
        "reply_discarded": False,
    }


# Node "agent": one model call with the system prompt in front of the conversation
def call_model(state: AgentState) -> dict:
    # Step 1: build the system prompt for this ticket (not stored in state)
    system_message = SystemMessage(content=SYSTEM_PROMPT.format(ticket_subject=state["ticket_subject"]))

    # Step 2: call the model; it either answers or asks for tool calls
    response = model_with_tools.invoke([system_message, *state["messages"]])

    # Step 3: add the response and count its tool calls
    return {"messages": [response], "tool_call_count": state["tool_call_count"] + len(response.tool_calls)}


# The ToolNode's error handler. LangGraph reads the type annotation and turns ONLY these two exception types into
# a tool message for the model: a ToolCallError (a business answer from a service, such as "order not found") and
# a ToolInvocationError (the model's arguments failed the Pydantic validation). Every other exception (a service
# down, a deadline exceeded, an internal error) propagates: the run fails and the Celery task retries.
def tool_error_message(error: ToolCallError | ToolInvocationError) -> str:
    return str(error)


# Node "post_reply": post the final answer on the ticket as an "ai" message
def post_reply(state: AgentState) -> dict:
    # Step 1: the last message is the model's final answer (fallback text if it is empty)
    content = state["messages"][-1].text.strip()
    if not content:
        content = FALLBACK_MESSAGE

    # Step 2: post it through the Ticket service, which publishes ticket.message_created for the browsers.
    # M10: if a human took over the ticket while this run was working, the Ticket service rejects the message
    # (ToolCallError). The reply is then discarded (kept only in reply_text) and the run finishes normally.
    # Any other error propagates as before.
    reply_text = content[:MAX_REPLY_LENGTH]
    reply_message_id = None
    reply_discarded = False
    try:
        message = clients.post_message(state["ticket_id"], "ai", reply_text)
        reply_message_id = message["id"]
    except ToolCallError as error:
        logger.warning("Ticket %s: reply discarded, the Ticket service rejected it: %s", state["ticket_id"], error)
        reply_discarded = True

    # Step 3: did the model escalate in this run? load_context replaced the old messages with the ticket's
    # messages (which carry no tool calls), so every tool call in the state is from this run; the last one counts.
    escalation_reason = None
    for state_message in state["messages"]:
        if isinstance(state_message, AIMessage):
            for tool_call in state_message.tool_calls:
                if tool_call["name"] == "escalate_to_human":
                    escalation_reason = str(tool_call["args"].get("reason", "")).strip() or None

    # Step 4 (M9): did a request_refund in this run create a PENDING approval? Its ToolMessage carries the approval
    # id in the artifact (never shown to the model). An error result has no artifact.
    pending_approval_id = None
    for state_message in state["messages"]:
        if isinstance(state_message, ToolMessage) and state_message.name == "request_refund":
            artifact = state_message.artifact
            if isinstance(artifact, dict) and artifact.get("status") == "pending":
                pending_approval_id = artifact["approval_id"]
    return {
        "reply_message_id": reply_message_id,
        "reply_text": reply_text,
        "reply_discarded": reply_discarded,
        "escalation_reason": escalation_reason,
        "pending_approval_id": pending_approval_id,
    }


# Edge after post_reply: pause for a pending refund approval, otherwise the run is over
def route_after_reply(state: AgentState) -> str:
    if state["pending_approval_id"] is not None:
        return "wait_for_approval"
    return END


# Node "wait_for_approval": pause the thread until a human decides. interrupt() saves the state and stops the run;
# on resume LangGraph runs this node again from the top and interrupt() returns the resume value instead.
def wait_for_approval(state: AgentState) -> dict:
    decision = interrupt({"approval_id": state["pending_approval_id"]})
    return {"approval_decision": decision}


# Node "announce_decision": post the outcome of the refund as a fixed message (no model call, so it is always exact)
def announce_decision(state: AgentState) -> dict:
    # Step 1: only the approval this thread waits for may be announced
    resumed_approval_id = (state["approval_decision"] or {}).get("approval_id")
    if resumed_approval_id != state["pending_approval_id"]:
        logger.warning(
            "Ticket %s was resumed for approval %s but waits for %s; nothing posted",
            state["ticket_id"],
            resumed_approval_id,
            state["pending_approval_id"],
        )
        return {"reply_message_id": None, "reply_text": None, "reply_discarded": False}

    # Step 2: read the decision from the Ticket service (the source of truth)
    approval = clients.get_approval(state["pending_approval_id"])

    # Step 3: fill in the template (amount like 18,000.00; the note's own final full stop is dropped, the template
    # adds one)
    if approval["status"] == "approved":
        amount = f"{Decimal(approval['amount']):,.2f}"
        content = REFUND_APPROVED_MESSAGE.format(amount=amount, summary=approval["summary"])
    else:
        decision_note = (approval["decision_note"] or "No reason was given").strip().rstrip(".")
        content = REFUND_REJECTED_MESSAGE.format(summary=approval["summary"], decision_note=decision_note)

    # Step 4: post it as an ai message and stop waiting. M10: a ticket a human has taken over rejects it
    # (ToolCallError); the outcome is then discarded (kept only in reply_text) and the human tells the customer.
    reply_text = content[:MAX_REPLY_LENGTH]
    reply_message_id = None
    reply_discarded = False
    try:
        message = clients.post_message(state["ticket_id"], "ai", reply_text)
        reply_message_id = message["id"]
    except ToolCallError as error:
        logger.warning("Ticket %s: outcome message discarded, the Ticket service rejected it: %s", state["ticket_id"], error)
        reply_discarded = True
    return {
        "reply_message_id": reply_message_id,
        "reply_text": reply_text,
        "reply_discarded": reply_discarded,
        "pending_approval_id": None,
    }


# Builds the model, the checkpointer and the compiled graph on the first call in each process, then returns the
# graph. Shared by the two entry points below.
def get_compiled_graph() -> CompiledStateGraph:
    global model_with_tools, compiled_graph

    if compiled_graph is None:
        # Step 1 (first run in this process): a psycopg connection pool on agent_db with the settings the
        # checkpointer needs; DATABASE_URL is in SQLAlchemy form, psycopg wants a plain postgresql:// URL
        connection_pool = ConnectionPool(
            conninfo=settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1),
            min_size=1,
            max_size=CHECKPOINT_POOL_MAX_SIZE,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=True,
        )

        # Step 2: the Postgres checkpointer; setup() creates its tables and is safe to call again
        checkpointer = PostgresSaver(connection_pool)
        checkpointer.setup()

        # Step 3: the chat model with the tools bound (no temperature: some newer models reject it)
        model = ChatOpenAI(
            model=settings.chat_model,
            api_key=settings.openai_api_key,
            timeout=MODEL_TIMEOUT_SECONDS,
            max_retries=MODEL_MAX_RETRIES,
        )
        model_with_tools = model.bind_tools(TOOLS)

        # Step 4: nodes and edges. tools_condition returns "tools" when the last AI message has tool calls,
        # otherwise END, which we route to post_reply instead.
        builder = StateGraph(AgentState)
        builder.add_node("load_context", load_context)
        builder.add_node("agent", call_model)
        builder.add_node("tools", ToolNode(TOOLS, handle_tool_errors=tool_error_message))
        builder.add_node("post_reply", post_reply)
        builder.add_node("wait_for_approval", wait_for_approval)
        builder.add_node("announce_decision", announce_decision)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "agent")
        builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "post_reply"})
        builder.add_edge("tools", "agent")
        builder.add_conditional_edges(
            "post_reply", route_after_reply, {"wait_for_approval": "wait_for_approval", END: END}
        )
        builder.add_edge("wait_for_approval", "announce_decision")
        builder.add_edge("announce_decision", END)
        compiled_graph = builder.compile(checkpointer=checkpointer)

    return compiled_graph


# Runs the graph for one customer message and returns what the run service records. paused_for_approval_id is set
# when the run ended paused in wait_for_approval (a refund waits for a human). M10: also returns the activity log
# of the run (tool_calls, retrieved_chunks, reply_text, model_name, input_tokens, output_tokens) and
# reply_discarded.
def run_agent(ticket_id: str) -> dict:
    # Step 1: the compiled graph (built on the first run in this process)
    graph = get_compiled_graph()

    # Step 2: run it. The ticket is the conversation thread; the recursion limit is the hard step limit
    # (it raises GraphRecursionError, which run_service handles).
    config = {"configurable": {"thread_id": ticket_id}, "recursion_limit": settings.agent_recursion_limit}
    final_state = graph.invoke({"ticket_id": ticket_id}, config)

    # Step 3: a paused run returns its state plus "__interrupt__" (the interrupt values)
    paused_for_approval_id = None
    if "__interrupt__" in final_state:
        paused_for_approval_id = final_state["pending_approval_id"]

    # Step 4 (M10): the messages this run added are exactly the ones after the history load_context loaded
    run_messages = final_state["messages"][final_state["history_count"]:]

    # Step 5: index this run's tool results by tool_call_id, so each tool call of the model finds its result
    tool_messages = {}
    for message in run_messages:
        if isinstance(message, ToolMessage):
            tool_messages[message.tool_call_id] = message

    # Step 6: walk the model's responses in order. Each response is one step: add up its token usage, and log every
    # tool call it asked for with its tier and result. The arguments are what the model sent; the injected
    # customer_id and ticket_id are added later by the ToolNode, so they never appear here.
    tool_calls = []
    retrieved_chunks = []
    model_name = None
    input_tokens = 0
    output_tokens = 0
    step = 0
    for message in run_messages:
        if not isinstance(message, AIMessage):
            continue
        step += 1
        model_name = message.response_metadata.get("model_name") or settings.chat_model
        if message.usage_metadata:
            input_tokens += message.usage_metadata.get("input_tokens", 0)
            output_tokens += message.usage_metadata.get("output_tokens", 0)
        for tool_call in message.tool_calls:
            tool_message = tool_messages.get(tool_call["id"])
            result = None
            is_error = False
            if tool_message is not None:
                result = tool_message.text[:MAX_LOGGED_RESULT_LENGTH]
                is_error = tool_message.status == "error"
            tool_calls.append({
                "step": step,
                "tool": tool_call["name"],
                "tier": TOOL_TIERS.get(tool_call["name"]),
                "arguments": tool_call["args"],
                "result": result,
                "is_error": is_error,
            })

            # Step 7: a knowledge-base search carries its raw results (with scores) in the ToolMessage artifact
            if tool_call["name"] == "search_knowledge_base" and tool_message is not None:
                if isinstance(tool_message.artifact, list):
                    for chunk in tool_message.artifact:
                        retrieved_chunks.append({
                            "query": tool_call["args"].get("query"),
                            "document_id": chunk["document_id"],
                            "title": chunk["title"],
                            "section": chunk["section"],
                            "score": chunk["score"],
                        })

    # Step 8: return what the run service records
    return {
        "context_until": final_state["context_until"],
        "reply_message_id": final_state["reply_message_id"],
        "reply_text": final_state["reply_text"],
        "reply_discarded": final_state["reply_discarded"],
        "tool_call_count": final_state["tool_call_count"],
        "escalation_reason": final_state["escalation_reason"],
        "paused_for_approval_id": paused_for_approval_id,
        "tool_calls": tool_calls,
        "retrieved_chunks": retrieved_chunks,
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# Resumes a thread that is paused for this approval and returns {"resumed": True, "reply_message_id", "reply_text",
# "reply_discarded"}, or "resumed": False when the thread is not waiting for it (an approval created over REST,
# a duplicate event, or an approval the thread no longer waits for). M10: reply_discarded is True when the outcome
# message was rejected because a human had taken over the ticket.
def resume_agent(ticket_id: str, approval_id: str) -> dict:
    # Step 1: the compiled graph and the thread's saved state
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": ticket_id}, "recursion_limit": settings.agent_recursion_limit}
    snapshot = graph.get_state(config)

    # Step 2: is the thread paused in wait_for_approval for THIS approval?
    waiting = False
    for pending_interrupt in snapshot.interrupts:
        if isinstance(pending_interrupt.value, dict) and pending_interrupt.value.get("approval_id") == approval_id:
            waiting = True

    # Step 3: a retry after announce_decision failed (for example the Ticket service was down): the interrupt is
    # already answered and announce_decision is the next node, so continue from the saved state with no new input
    retrying_announcement = (
        "announce_decision" in snapshot.next and snapshot.values.get("pending_approval_id") == approval_id
    )

    # Step 4: resume with the approval id (or continue the failed announcement); otherwise there is nothing to do
    if waiting:
        final_state = graph.invoke(Command(resume={"approval_id": approval_id}), config)
    elif retrying_announcement:
        final_state = graph.invoke(None, config)
    else:
        return {"resumed": False, "reply_message_id": None, "reply_text": None, "reply_discarded": False}

    # Step 5: the outcome message posted (or discarded) by announce_decision
    logger.info(
        "Ticket %s resumed for approval %s, outcome message %s", ticket_id, approval_id, final_state["reply_message_id"]
    )
    return {
        "resumed": final_state["reply_message_id"] is not None or final_state["reply_discarded"],
        "reply_message_id": final_state["reply_message_id"],
        "reply_text": final_state["reply_text"],
        "reply_discarded": final_state["reply_discarded"],
    }
