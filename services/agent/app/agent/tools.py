# The tools the model may call. Each one validates its input with Pydantic, calls a service through a gRPC
# client, and returns text for the model. Tools never touch a database. M8 adds the order tools to TOOLS.
from typing import Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, Field, StringConstraints

from app.agent import clients

SEARCH_TOP_K = 4


class SearchKnowledgeBaseInput(BaseModel):
    # 1 to 500 visible characters (surrounding spaces are removed), the Knowledge service's limit
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="A short search query in plain words, for example 'return window for electronics'.",
    )


@tool(args_schema=SearchKnowledgeBaseInput)
def search_knowledge_base(query: str) -> str:
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
        return "No relevant policy was found for this query."

    # Step 2: one numbered block per result: "[1] Title > Section", then the chunk text
    # (the stored chunk text starts with the same "Title > Section" line, so it is removed there)
    blocks = []
    for number, result in enumerate(results, start=1):
        heading = f"{result['title']} > {result['section']}"
        text = result["content"].removeprefix(heading).strip()
        blocks.append(f"[{number}] {heading}\n{text}")
    return "\n\n".join(blocks)


# The list graph.py binds to the model and gives to the ToolNode
TOOLS = [search_knowledge_base]
