# The AI agent: a LangGraph StateGraph, checkpointed in Postgres with thread_id = ticket_id.
#
#   START -> load_context -> agent -> (tool calls?) -> tools -> agent -> ... -> post_reply -> END
#
# run_agent(ticket_id) is the one entry point (called by run_service). The node functions below are graph entry points.
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolInvocationError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.agent import clients
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOLS
from app.config import settings
from app.errors import ToolCallError

# Posted when the model returns an empty answer, after the final failed retry, and when the step limit is hit
FALLBACK_MESSAGE = "Sorry, I'm having trouble answering right now. A support agent will follow up with you."
MODEL_TIMEOUT_SECONDS = 30
MODEL_MAX_RETRIES = 2
# The Ticket service accepts messages of at most 5000 characters
MAX_REPLY_LENGTH = 5000
CHECKPOINT_POOL_MAX_SIZE = 2


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

    # Step 2: post it through the Ticket service, which publishes ticket.message_created for the browsers
    message = clients.post_message(state["ticket_id"], "ai", content[:MAX_REPLY_LENGTH])

    # Step 3: did the model escalate in this run? load_context replaced the old messages with the ticket's
    # messages (which carry no tool calls), so every tool call in the state is from this run; the last one counts.
    escalation_reason = None
    for state_message in state["messages"]:
        if isinstance(state_message, AIMessage):
            for tool_call in state_message.tool_calls:
                if tool_call["name"] == "escalate_to_human":
                    escalation_reason = str(tool_call["args"].get("reason", "")).strip() or None
    return {"reply_message_id": message["id"], "escalation_reason": escalation_reason}


# Runs the graph for one ticket and returns what the run service records (including the escalation reason)
def run_agent(ticket_id: str) -> dict:
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
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "agent")
        builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "post_reply"})
        builder.add_edge("tools", "agent")
        builder.add_edge("post_reply", END)
        compiled_graph = builder.compile(checkpointer=checkpointer)

    # Step 5: run it. The ticket is the conversation thread; the recursion limit is the hard step limit
    # (it raises GraphRecursionError, which run_service handles).
    config = {"configurable": {"thread_id": ticket_id}, "recursion_limit": settings.agent_recursion_limit}
    final_state = compiled_graph.invoke({"ticket_id": ticket_id}, config)

    # Step 6: return what the run service records
    return {
        "context_until": final_state["context_until"],
        "reply_message_id": final_state["reply_message_id"],
        "tool_call_count": final_state["tool_call_count"],
        "escalation_reason": final_state["escalation_reason"],
    }
