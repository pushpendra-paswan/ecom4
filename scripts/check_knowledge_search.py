# Checks that knowledge search finds the right policy document for ten typical customer questions.
# Run from the host after seeding: python scripts/check_knowledge_search.py
# Standard library only. Talks to the running stack through Nginx and logs in as agent1 (search is staff-only).
import json
import urllib.request

BASE_URL = "http://localhost:8080"
EMAIL = "agent1@example.com"
PASSWORD = "Password123!"
TOP_K = 3

# Each question and the slug of the document that should answer it
QUESTIONS = [
    ("How many days do I have to return a product?", "returns-policy"),
    ("Can I return headphones after 20 days?", "returns-policy"),
    ("Can I return a gift card?", "returns-policy"),
    ("How long does a refund take to reach my account?", "refund-policy"),
    ("Do large refunds need approval?", "refund-policy"),
    ("How long does standard delivery take?", "shipping-policy"),
    ("How can I track my order?", "shipping-policy"),
    ("Can I cancel an order after it has shipped?", "cancellation-policy"),
    ("What does the warranty cover on a smartwatch?", "warranty-policy"),
    ("What payment methods do you accept?", "general-faq"),
]

# Step 1: log in and keep the token
login_body = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
login_request = urllib.request.Request(
    f"{BASE_URL}/api/identity/login", data=login_body, headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(login_request) as response:
    token = json.load(response)["access_token"]
auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Step 2: search results only carry the document id, so load the documents to map id -> slug
documents_request = urllib.request.Request(f"{BASE_URL}/api/knowledge/documents", headers=auth_headers)
with urllib.request.urlopen(documents_request) as response:
    slug_by_id = {document["id"]: document["slug"] for document in json.load(response)}

# Step 3: ask each question and compare the top results with the expected document
rank_1_passes = 0
top_3_passes = 0
for question, expected_slug in QUESTIONS:
    search_body = json.dumps({"query": question, "top_k": TOP_K}).encode()
    search_request = urllib.request.Request(
        f"{BASE_URL}/api/knowledge/search", data=search_body, headers=auth_headers
    )
    with urllib.request.urlopen(search_request) as response:
        results = json.load(response)["results"]

    result_slugs = [slug_by_id.get(result["document_id"], result["title"]) for result in results]
    rank_1_ok = len(result_slugs) > 0 and result_slugs[0] == expected_slug
    top_3_ok = expected_slug in result_slugs
    rank_1_passes += rank_1_ok
    top_3_passes += top_3_ok

    top_slug = result_slugs[0] if result_slugs else "(no results)"
    top_section = results[0]["section"] if results else ""
    top_score = results[0]["score"] if results else 0
    print(question)
    print(f"  top: {top_slug} / {top_section} (score {top_score})   expected: {expected_slug}")
    print(f"  rank 1: {'PASS' if rank_1_ok else 'FAIL'}   top 3: {'PASS' if top_3_ok else 'FAIL'}   top 3 = {result_slugs}")

# Step 4: print the summary
total = len(QUESTIONS)
print()
print(f"Summary: rank 1 {rank_1_passes}/{total}, top 3 {top_3_passes}/{total} "
      f"(target: rank 1 >= 9/{total}, top 3 = {total}/{total})")
