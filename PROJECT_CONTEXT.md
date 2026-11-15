# PROJECT_CONTEXT.md

Living source of truth. Updated at the end of every milestone. Rules live in `CLAUDE.md`.

## 1. Overview

An AI Customer Support Automation Platform for a simulated e-commerce company, built as a learning project. Customers open tickets and chat with an AI assistant (RAG + tool calling) that can look up orders and request returns or refunds; sensitive actions need human approval in an agent dashboard. Orders and refunds are mock data.

## 2. Architecture and communication

- Browser → Nginx (`localhost:8080`) → services over REST. Nginx strips the prefix (`/api/orders/health` reaches the order service as `/health`).
- WebSockets: `/ws/<path>` → realtime service as `/<path>`, with upgrade headers. The one endpoint is `ws://localhost:8080/ws/connect` (FastAPI path `/connect`).
- Events (M6): the Ticket service publishes domain events to the RabbitMQ topic exchange `support.events` right after each commit (`pika`, one short-lived connection per publish). The Realtime service (async, `aio-pika`) consumes them from one shared durable queue `realtime.events` (competing consumers: each event goes to one instance), republishes each event to the Redis pub/sub channel `realtime:events`, and every Realtime instance delivers what it receives on that channel to its own WebSocket connections. So each event reaches each connection once, whichever instance holds it. Events carry IDs only; browsers refetch details over REST.
- Celery (M4): the knowledge service queues document ingestion on RabbitMQ (Celery's default `celery` queue, not the events exchange); the `knowledge-worker` container runs it; Redis is the Celery result backend.
- gRPC (M5): Order (50051), Knowledge (50052) and Ticket (50053) run a synchronous gRPC server **in the same container and process** as their FastAPI app, beside REST. Servicers are thin entry points that call the same service functions as the routers. Every call carries a short-lived HS256 service token checked by a server interceptor. The only intended caller so far is the agent service (M7+); today only `scripts/grpc_client_check.py` calls them.
- Planned (not built yet): Redis per-ticket locks for agent runs (M7); event consumers in the agent (M7, `ticket.message_created`) and order (M9, `approval.decided`) services.
- Auth: Identity signs RS256 JWTs with a private key; every other service verifies them locally with the public key (no call to Identity).
- Current state (M6): Identity, Order, Ticket and Knowledge have databases and real REST endpoints; Order, Knowledge and Ticket also serve gRPC. No service calls another over gRPC yet. Knowledge also has a Celery worker and RAG search (OpenAI embeddings in pgvector). Ticket publishes events; Realtime pushes them to browsers over WebSockets and can run as several instances. Agent only exposes `GET /health`. RabbitMQ carries Celery tasks and events; Redis is the Celery result backend and the Realtime fan-out channel.

## 3. Tech stack

Python 3.12, FastAPI 0.162.7, Uvicorn 0.54.0, pydantic-settings 2.16.3. Postgres 17 + pgvector 0.8.2 (`pgvector/pgvector:0.8.2-pg17`), Redis 7.4 (`redis:7.4-alpine`), RabbitMQ 4.2 (`rabbitmq:4.2-management-alpine`), Nginx 1.28 (`nginx:1.28-alpine`). Docker Compose. Plain HTML/CSS/JS frontends.
Identity (M1) also uses SQLAlchemy 2.1.8 (sync), psycopg 3.3.9 (binary), Alembic 1.20.1, PyJWT 2.15.1, cryptography 50.0.2, argon2-cffi 25.1.0, email-validator 2.3.0.
Order (M2) and Ticket (M3) use the same pinned SQLAlchemy, psycopg, Alembic, PyJWT and cryptography versions as Identity.
Knowledge (M4) uses the same database/JWT pins plus Celery 5.7.0, redis 8.1.0, langchain-openai 1.8.0, langchain-core 1.8.0, langchain-text-splitters 1.1.3, langchain-postgres 0.0.19 (pulls in openai, pgvector-python, tiktoken, numpy).
Order, Knowledge and Ticket (M5) add grpcio 1.85.1 and protobuf 7.35.1. Host only (never in a service): grpcio-tools 1.85.1 (bundles protoc 35.1) for code generation, plus PyJWT 2.15.1 for the check script.
Ticket (M6) adds pika 1.4.4. Realtime (M6) uses FastAPI, `uvicorn[standard]` 0.54.0 (WebSocket support), pydantic-settings, aio-pika 10.2.3, redis 8.1.0 (asyncio client), PyJWT 2.15.1, cryptography 50.0.2. No database.
Later milestones add: LangGraph (see `CLAUDE.md` section 7).

## 4. Services

| Service | Purpose | REST port | gRPC port | Database | Nginx prefix |
|---|---|---|---|---|---|
| identity | Users, login, JWT issuing | 8001 | – | identity_db | `/api/identity/` |
| ticket | Tickets, messages, approvals | 8002 | 50053 | ticket_db | `/api/tickets/` |
| order | Mock orders, returns, simulated refunds | 8003 | 50051 | order_db | `/api/orders/` |
| knowledge | Policy documents, RAG search | 8004 | 50052 | knowledge_db (pgvector) | `/api/knowledge/` |
| agent | LangGraph AI agent | 8005 | – | agent_db | `/api/agent/` |
| realtime | WebSocket push to browsers (scalable: `--scale realtime=2`) | 8006 | – | none | `/ws/` |

Published to the host: Nginx `8080`, RabbitMQ management UI `15672`, and the three gRPC ports **bound to `127.0.0.1` only** (`127.0.0.1:50051:50051` etc.) so the host-side check script can reach them; this is a development-only choice (other containers use `order:50051`, `knowledge:50052`, `ticket:50053`). REST service ports, Postgres, Redis and AMQP are internal only.

## 5. Repository structure

```
.
├── CLAUDE.md, PROJECT_CONTEXT.md, README.md
├── docker-compose.yml, .env.example, .env (not committed), .gitignore
├── keys/                      # not committed: private/private.pem, public/public.pem
├── nginx/nginx.conf
├── proto/                     # M5: order.proto, knowledge.proto, ticket.proto (source of truth for gRPC)
├── scripts/
│   ├── init_databases.sql     # Postgres init: 5 databases + vector extension
│   ├── generate_keys.py       # creates the RSA key pair in keys/
│   ├── check_knowledge_search.py  # M4: 10 questions against /api/knowledge/search (stdlib only, run on the host)
│   ├── generate_proto.py      # M5: protoc → services/<order|knowledge|ticket>/app/generated/ and scripts/generated/
│   ├── grpc_client_check.py   # M5: PASS/FAIL check of every RPC, deadlines, validation and service tokens (host)
│   └── generated/             # M5: generated stubs for all three protos (committed)
├── frontend/
│   ├── customer/  index.html, app.js (placeholder health check)
│   ├── agent/     index.html, app.js (placeholder health check)
│   └── test/      realtime.html   # M6 THROWAWAY WebSocket test page; delete (with nginx's /test/ location) once M11/M12 exist
└── services/<identity|ticket|order|knowledge|agent|realtime>/
    ├── Dockerfile             # identical in all six
    ├── requirements.txt       # M0 packages only for agent; the others add what they use (see section 3)
    └── app/
        ├── main.py            # FastAPI app + GET /health
        └── config.py          # pydantic-settings

services/realtime/ (M6, no database, the only async service):
    └── app/
        ├── main.py            # FastAPI app, GET /health, lifespan starts/stops the two background tasks
        ├── config.py          # RABBITMQ_URL, REDIS_URL, JWT_PUBLIC_KEY_PATH, instance_id (= hostname)
        ├── auth.py            # only decode_access_token(token) (deviation from the auth.py copy contract)
        ├── connections.py     # in-memory rooms: user_rooms {user_id: set of sockets}, staff_room (set); add, remove, deliver_to_rooms
        ├── events.py          # consume_rabbitmq_events (aio-pika → Redis) and subscribe_redis_events (Redis → local sockets)
        └── routers/ws_router.py   # WebSocket /connect: auth handshake, ping/pong, cleanup

services/ticket/ (M6) additionally has app/events.py: publish_event(event_type, data) (pika, called by the services after each commit).

services/order/, services/knowledge/, services/ticket/ (M5) additionally have:
    └── app/
        ├── grpc_server.py     # servicer, abort_from_error, ServiceTokenInterceptor, start/stop_grpc_server
        └── generated/         # <name>_pb2.py, <name>_pb2_grpc.py (generated, committed; do not edit)

services/knowledge/ (M4) additionally has:
    ├── alembic.ini, alembic/  # same pattern; env.py ignores langchain_pg_* tables; versions/0001_create_documents.py
    ├── seed.py                # creates the six policy documents from seed_docs/ (run inside the container)
    ├── seed_docs/             # six customer-facing markdown files; file name = slug
    └── app/
        ├── database.py, models.py, schemas.py, errors.py, auth.py   # database/errors/auth copied from ticket (= identity)
        ├── rag.py             # ALL RAG code: chunk, embed, store, delete, search (LangChain + PGVector)
        ├── celery_app.py      # Celery app (RabbitMQ broker, Redis results)
        ├── tasks.py           # ingest_document task
        ├── routers/       document_router.py, search_router.py
        ├── services/      document_service.py, search_service.py
        └── repositories/  document_repository.py

services/ticket/ (M3) additionally has:
    ├── alembic.ini, alembic/  # same pattern; versions/0001_create_ticket_tables.py
    ├── seed.py                # three demo tickets with messages (run inside the container)
    └── app/
        ├── database.py, models.py, schemas.py, errors.py, auth.py   # database/errors/auth copied from order (= identity)
        ├── routers/       ticket_router.py, approval_router.py
        ├── services/      ticket_service.py, approval_service.py
        └── repositories/  ticket_repository.py, message_repository.py, approval_repository.py

services/order/ (M2) additionally has:
    ├── alembic.ini, alembic/  # same pattern as identity; versions/0001_create_order_tables.py
    ├── seed.py                # ten demo orders (run inside the container)
    └── app/
        ├── database.py, models.py, schemas.py, errors.py, auth.py   # database/errors/auth copied from identity
        ├── routers/       order_router.py, return_router.py, refund_router.py
        ├── services/      order_service.py, return_service.py, refund_service.py
        └── repositories/  order_repository.py, return_repository.py, refund_repository.py

services/identity/ (M1) additionally has:
    ├── alembic.ini, alembic/  # env.py reads DATABASE_URL from app.config; versions/0001_create_users.py
    ├── seed.py                # demo users (run inside the container)
    └── app/
        ├── database.py, models.py, schemas.py, errors.py, auth.py
        ├── routers/       auth_router.py, user_router.py
        ├── services/      auth_service.py, user_service.py
        └── repositories/  user_repository.py
```

## 6. Data model

Databases created by `scripts/init_databases.sql`: `identity_db`, `ticket_db`, `order_db`, `knowledge_db` (with `vector` extension), `agent_db`. `identity_db`, `order_db`, `ticket_db` and `knowledge_db` have tables so far.

**identity_db**
- `users`: `id` (UUID PK), `email` (varchar 255, unique index `ix_users_email`), `full_name` (200), `password_hash` (argon2), `role` (`customer` | `agent` | `admin`, check constraint), `created_at` (timestamptz, default `now()`).

**order_db** (UUID primary keys, money `Numeric(10,2)`, statuses and categories are plain varchar; `customer_id` has no foreign key because customers live in identity_db)
- `orders`: `id`, `order_number` (unique, `ORD-1001`), `customer_id` (indexed), `status` (`placed` | `shipped` | `delivered` | `cancelled`), `total_amount`, `ordered_at` (timestamptz).
- `order_items`: `id`, `order_id` (FK, on delete cascade, indexed), `product_name`, `category`, `quantity`, `unit_price`.
- `shipments`: `id`, `order_id` (FK, unique: one per order), `carrier`, `tracking_number`, `status` (`in_transit` | `delivered`), `shipped_on`, `estimated_delivery`, `delivered_on` (nullable). All dates are `date`.
- `return_requests`: `id`, `order_id` (FK), `order_item_id` (FK, **unique**: an item is returned once), `customer_id` (indexed), `reason`, `refund_amount`, `status` (`requested` | `refunded`), `created_at`.
- `refunds`: `id`, `return_request_id` (FK, **unique**), `approval_id` (**unique**), `amount`, `status` (`completed`), `created_at`.

**ticket_db** (UUID primary keys, statuses plain varchar; customers and agents are referenced by UUID only, no foreign keys to other services)
- `tickets`: `id`, `customer_id` (indexed), `customer_email` (copied from the JWT `email` claim at creation), `subject` (≤200), `status` (`open` | `awaiting_approval` | `escalated` | `resolved`), `assigned_agent_id` (nullable), `created_at`, `updated_at` (set to `now()` whenever a message is added or the status or assignment changes).
- `messages`: `id`, `ticket_id` (FK, on delete cascade, indexed), `sender_type` (`customer` | `ai` | `agent` | `system`), `sender_id` (nullable; null for `ai`/`system`), `content` (text), `created_at`.
- `approvals`: `id` (= the `approval_id` the order service will receive), `ticket_id` (FK), `action_type` (`refund`), `return_request_id` (UUID, **unique**, no FK: it lives in order_db), `amount` (`Numeric(10,2)`), `summary` (text), `status` (`pending` | `approved` | `rejected`), `auto_approved` (bool), `decided_by` (nullable), `decision_note` (nullable), `created_at`, `decided_at` (nullable; also set for auto-approvals).

**knowledge_db**
- `documents`: `id` (UUID PK), `slug` (varchar 100, **unique**, lowercase letters/digits/hyphens), `title` (200), `content` (text, markdown), `status` (`pending` | `processing` | `ready` | `failed`), `error_message` (text, nullable; last ingestion error), `chunk_count` (int, default 0), `created_at`, `updated_at` (set to `now()` on every status change).
- `langchain_pg_collection`, `langchain_pg_embedding`: created and owned by LangChain's `PGVector` (not by Alembic; `alembic/env.py` has an `include_object` that skips `langchain_pg_*`, and `alembic check` reports no changes). Embedding rows: `id` (string, our chunk id), `collection_id`, `embedding` (`vector(1536)`), `document` (chunk text), `cmetadata` (JSONB). The knowledge API creates these tables and the collection at startup (`rag.open_vector_store()` in `main.py`).

## 7. Contracts

**REST** (every service): `GET /health` → `{"service": "<name>", "status": "ok"}`. Swagger at `/api/<prefix>/docs` through Nginx.

**REST, identity** (through Nginx: `/api/identity/...`):
| Endpoint | Auth | Result |
|---|---|---|
| `POST /register` `{email, password (min 8), full_name}` | none | 201 user (role always `customer`); 409 if email exists; 422 on invalid input |
| `POST /login` `{email, password}` | none | 200 `{access_token, token_type: "bearer"}`; 401 `Invalid email or password` for any failure |
| `GET /me` | any valid token | 200 own profile (no `password_hash`) |
| `GET /users` | role `admin` | 200 list of all users; 403 for other roles |

Error mapping (identity `main.py`): `NotFoundError` 404, `UnauthorizedError` 401 (with `WWW-Authenticate: Bearer`), `ForbiddenError` 403, `ConflictError` 409, `BadRequestError` 400. Missing, invalid, expired or wrong-algorithm token → 401; wrong role → 403.

**JWT:** RS256, one access token, no refresh. Claims: `sub` (user id), `email`, `role`, `iat`, `exp`. Lifetime from `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60). `scripts/generate_keys.py` writes a 2048-bit RSA pair to `keys/private/private.pem` and `keys/public/public.pem` (refuses to overwrite existing keys). Compose mounts the two **directories** read-only at `/keys/private` and `/keys/public`; only identity mounts the private one. Identity reads both files once at import time and stops with a clear `RuntimeError` if one is missing.

**`auth.py` copy contract** (for the other services, M2 onwards):
- Copy `services/identity/app/auth.py` unchanged. It provides `get_current_user` (returns `{user_id, email, role}`) and `require_role(*roles)`.
- The target service needs: `app/errors.py` with `ForbiddenError` and `UnauthorizedError` (copy identity's `errors.py`), `settings.jwt_public_key_path` in its `config.py`, `fastapi`, `PyJWT` and `cryptography` in `requirements.txt`.
- Compose for that service: mount `./keys/public:/keys/public:ro` (never the private folder) and set `JWT_PUBLIC_KEY_PATH=${JWT_PUBLIC_KEY_PATH}`.
- The service's `main.py` must register exception handlers for `UnauthorizedError` (401) and `ForbiddenError` (403), like identity's.

**Demo users** (`docker compose exec identity python seed.py`, all with password `Password123!`, defined as `DEMO_PASSWORD` in `seed.py`):
| Email | Role | Fixed id |
|---|---|---|
| admin@example.com | admin | `00000000-0000-0000-0000-000000000001` |
| agent1@example.com | agent | `00000000-0000-0000-0000-000000000011` |
| agent2@example.com | agent | `00000000-0000-0000-0000-000000000012` |
| customer1@example.com | customer | `00000000-0000-0000-0000-000000000101` |
| customer2@example.com | customer | `00000000-0000-0000-0000-000000000102` |
| customer3@example.com | customer | `00000000-0000-0000-0000-000000000103` |

**REST, order** (through Nginx: `/api/orders/...`). Missing, invalid or expired token → 401; wrong role → 403. A customer asking for someone else's order or return gets **404** (not 403). Amounts are JSON numbers (e.g. `4500.0`).
| Endpoint | Roles | Result |
|---|---|---|
| `GET /orders` | customer | 200 own orders (with items and shipment), ordered by order number |
| `GET /orders/{order_number}` | customer (own only), agent, admin (any) | 200 order with `items[]` and `shipment` (null for placed/cancelled); 404 |
| `GET /orders/{order_number}/items/{item_id}/return-eligibility` | customer (own only), agent, admin (any) | 200 eligibility result (below); 404 if order or item not found |
| `POST /returns` `{order_number, order_item_id, reason}` | customer | 201 return request (`status: requested`); 409 item already has a return; 400 not eligible (reason in `detail`); 404 not your order/item |
| `GET /returns` | customer | 200 own return requests, newest first |
| `GET /returns/{return_id}` | customer (own only), agent, admin (any) | 200 return request; 404 |
| `POST /refunds/execute` `{return_request_id, approval_id}` | admin | 201 new refund; **200** same `approval_id` again (`already_processed: true`, nothing created); 409 different approval for an already-refunded return, or an approval already used for another return; 404 unknown return |

**Return eligibility result** (gRPC `ReturnEligibility` has the same shape): `eligible` (bool), `reason_code` (`eligible` | `not_delivered` | `non_returnable` | `window_expired` | `already_requested`), `reason` (sentence), `refund_amount` (quantity × unit price, only when eligible, else null), `window_days` and `days_remaining` (set once the order is delivered and the category is returnable, else null; `days_remaining` is 0 when expired). Rules, checked in this order (constants at the top of `return_service.py`): item belongs to the order (else 404) → order is `delivered` (with a shipment delivery date) → category is not `gift_card`/`perishable` → `days_since_delivery <= window` (30 days, **15 for `electronics`**) → no existing return request for the item. Decided per whole order line, no partial quantities. `create_return_request` runs the same check again and never trusts an earlier one.

**Refund execution** (`refund_service.execute_refund`, simulated, logs one INFO line per refund): amount always comes from the return request. Idempotent through the unique constraints on `refunds.approval_id` and `refunds.return_request_id`: a repeated `approval_id` returns the existing refund; if two calls race, the loser's insert fails, it rolls back and returns the winner's refund (same approval) or a 409 (different approval). Verified with 8 parallel calls: exactly one 201. In M9 a Celery task will call this same function.

**REST, ticket** (through Nginx: `/api/tickets/...`). Missing, invalid or expired token → 401; wrong role → 403. A customer asking for someone else's ticket gets **404**. Staff = `agent` or `admin`.
| Endpoint | Roles | Result |
|---|---|---|
| `POST /tickets` `{subject (1–200), message (1–5000)}` | customer | 201 `{ticket, messages: [first message]}`; ticket and first message saved in one transaction |
| `GET /tickets?status=` | customer (own), staff (all) | 200 list of tickets, newest `updated_at` first; optional `status` filter (422 for an unknown status) |
| `GET /tickets/{ticket_id}` | customer (own), staff (any) | 200 `{ticket, messages}` (messages oldest first); 404 |
| `POST /tickets/{ticket_id}/messages` `{content (1–5000)}` | customer (own), staff (any) | 201 message; `sender_type` is `customer` for customers and `agent` for agents **and admins**; 404 |
| `POST /tickets/{ticket_id}/assign` | staff | 200 ticket assigned to the caller (re-assigning to yourself is allowed; overwrites another agent) |
| `PATCH /tickets/{ticket_id}/status` `{status}` | staff | 200 ticket; only `open`, `escalated`, `resolved` (anything else 422) |
| `POST /tickets/{ticket_id}/approvals` `{action_type: "refund" (default), return_request_id, amount (>0, 2 decimals), summary (1–1000)}` | **admin only** (testing entry point; the agent uses gRPC from M5/M9) | 201 approval; 404 unknown ticket; 409 return request already has an approval |
| `GET /approvals?status=` | staff | 200 list, newest first; optional `status` (`pending`/`approved`/`rejected`) |
| `GET /approvals/{approval_id}` | staff | 200 approval; 404 |
| `POST /approvals/{approval_id}/approve` `{note?}` (body optional) | staff | 200 approval (`approved`, `decided_by` = caller); 409 already decided; 404 |
| `POST /approvals/{approval_id}/reject` `{note}` (required, not blank) | staff | 200 approval (`rejected`, note stored); 422 missing/blank note; 409 already decided; 404 |
The REST API can never create `ai` or `system` messages. `ticket_service.add_message` takes `sender_type`/`sender_id` as parameters; gRPC `PostMessage` (M5) uses it to post `ai`/`system` messages.

**Ticket statuses and what changes them:**
- New ticket → `open`.
- Staff `PATCH .../status` → `open`, `escalated` or `resolved` (any current status).
- Customer message on a `resolved` ticket → `open` (staff messages never change the status).
- Approval created as `pending` → `awaiting_approval`. An auto-approved one leaves the ticket unchanged.
- Approval approved or rejected → `open`, but only if the ticket is currently `awaiting_approval`.

**Approval rules** (`approval_service.py`):
- Auto-approve: a `refund` approval with `amount <= REFUND_AUTO_APPROVE_LIMIT` (env, default 5000) is created as `approved`, `auto_approved=true`, `decided_by=null`, `decided_at` set. Above the limit it is `pending`.
- One approval per return request: checked first (409), and the unique constraint on `return_request_id` catches simultaneous creates (IntegrityError → rollback → 409). Verified: 6 parallel creates → one 201.
- Decisions load the approval with `SELECT ... FOR UPDATE` (`approval_repository.get_by_id_for_update`, with `populate_existing`), so a second decision waits for the first commit and then sees it is no longer `pending` → 409. Verified: 8 parallel approves → exactly one 200, seven 409.
- `reject` also checks the note in the service (BadRequestError, 400) for future gRPC callers; REST returns 422 before that.
- `approval_service` never calls `ticket_service`; both use the repositories directly.

**Demo tickets** (`docker compose exec ticket python seed.py`, fixed ids, existing ones skipped; times relative to when it runs):
| Ticket id | Customer | Subject | Status | Assigned | Messages |
|---|---|---|---|---|---|
| `00000000-0000-0000-0000-000000000201` | customer1 | Question about my headphones | open | agent1 | customer (ORD-1001 headphones crackle) + agent reply |
| `00000000-0000-0000-0000-000000000202` | customer2 | Smartwatch not working | open | – | customer (ORD-1004 smartwatch won't turn on) |
| `00000000-0000-0000-0000-000000000203` | customer3 | Where is my order? | resolved | – | customer (ORD-1009 not shipped) + agent1 reply |

**REST, knowledge** (through Nginx: `/api/knowledge/...`). Missing, invalid or expired token → 401; wrong role → 403; unknown id → 404; invalid input → 422. Staff = `agent` or `admin`. Customers have no access at all.
| Endpoint | Roles | Result |
|---|---|---|
| `POST /documents` `{slug (^[a-z0-9]+(-[a-z0-9]+)*$, ≤100), title (1–200), content (markdown, 1–100000)}` | admin | **202** document with `status: pending`, ingestion queued; 409 duplicate slug |
| `GET /documents` | staff | 200 list without `content` (`id, slug, title, status, error_message, chunk_count, created_at, updated_at`), by slug |
| `GET /documents/{document_id}` | staff | 200 document with `content`; 404 |
| `POST /documents/{document_id}/reingest` | admin | **202** document back to `pending` (chunk count kept until replaced), ingestion queued; allowed in any status |
| `DELETE /documents/{document_id}` | admin | **204**; chunks deleted from PGVector first, then the row |
| `POST /search` `{query (1–500), top_k (1–10, default 4)}` | staff | 200 `{results: [SearchResult]}`, best first. Staff-only because every search is a paid embedding call; the agent uses gRPC `KnowledgeService.Search` |
No content updates: delete and re-create a document to change it.

**SearchResult** (same fields in gRPC `knowledge.SearchResult`): `document_id` (UUID string), `title` (document title), `section` (the H2 heading the chunk came from, or `Overview` for text before the first H2), `content` (chunk text, starting with `Title > Section` and a blank line), `score` (float, 4 decimals). **Score = 1 − cosine distance** = cosine similarity: PGVector's default distance is cosine (0 = identical direction), so higher is better and 1.0 is a perfect match. Seed-doc matches for good questions score about 0.53–0.82.

**Ingestion flow:** create/reingest → service commits the row (`pending`) → `celery_app.send_task("app.tasks.ingest_document", [document_id])` → worker: `process_ingestion` sets `processing` → `rag.ingest_document_chunks` → `ready` + `chunk_count`, or on any error `failed` + `error_message` (`"<ErrorType>: <message>"`, ≤1000 chars), `chunk_count` 0, and the error is re-raised so Celery retries. Between retries the status shows `failed`, then `processing` again. A task for a deleted document logs a warning and does nothing.

**RAG settings** (`app/rag.py`, constants at the top):
- Split 1: `MarkdownHeaderTextSplitter` on `#` (→ metadata `title_heading`) and `##` (→ `section`), headers stripped from the text. Split 2: `RecursiveCharacterTextSplitter`, `CHUNK_SIZE = 800`, `CHUNK_OVERLAP = 100` characters.
- Chunk text = `"{title} > {section}\n\n{text}"`. Metadata: `document_id` (string), `title`, `section`.
- Embeddings: `OpenAIEmbeddings(model=EMBEDDING_MODEL)` (env, `text-embedding-3-small`), `EMBEDDING_DIMENSIONS = 1536`.
- Store: `langchain_postgres.PGVector`, `COLLECTION_NAME = "knowledge_base"`, `embedding_length=1536`, `use_jsonb=True`, `create_extension=False` (the init script creates `vector`). Connection: the service's SQLAlchemy engine (from the `postgresql+psycopg://` `DATABASE_URL`).
- **Chunk id = `"{document_id}:{chunk_number}"`** (0-based). Ingestion first deletes ids `0 … previous_chunk_count−1`, then adds the new chunks (PGVector upserts on id), so re-running never duplicates. Delete removes the same ids.
- Entry points: `ingest_document_chunks(document_id, title, content, previous_chunk_count) -> int`, `delete_document_chunks(document_id, chunk_count)`, `search_chunks(query, top_k) -> list[dict]`; plus `open_vector_store()` used by all three and at API startup.

**Celery** (first use in the project):
- `app/celery_app.py`: app `knowledge`, broker `RABBITMQ_URL`, result backend `REDIS_URL`, JSON-only serialization, UTC. `include=["app.tasks"]`.
- Task `app.tasks.ingest_document(document_id: str)`: opens its own DB session, calls `document_service.process_ingestion`, closes it. `autoretry_for=(Exception,)`, `retry_backoff=True` (with Celery's default jitter), `retry_backoff_max=60`, `max_retries=3` → at most 4 attempts.
- Sent by name with `send_task`, so `document_service` does not import `tasks.py` (that would be a circular import).
- Container `knowledge-worker`: same build as `knowledge`, command `celery -A app.celery_app worker --loglevel=info` (prefork, one process per CPU), no Beat, no Flower. Default queue `celery` on RabbitMQ (visible in the management UI).

**Seed documents** (`docker compose exec knowledge python seed.py`; slug = file name in `services/knowledge/seed_docs/`, title = the H1; existing slugs skipped, so nothing is re-embedded; 39 chunks in total). **These facts must stay consistent with the Order and Ticket rules** (`return_service.py` constants, `REFUND_AUTO_APPROVE_LIMIT`); change both together.
| Slug | Chunks | Key facts |
|---|---|---|
| `returns-policy` | 6 | 30 days from delivery, **15 for electronics**; only delivered items; per item; one return request per item; gift cards and perishables not returnable; unused, original packaging; start a return in the ticket chat with order number + item |
| `refund-policy` | 6 | refund = price paid for the item; **≤ ₹5,000 automatic**, **above ₹5,000 reviewed** by support (usually within one business day); 5–7 business days to the original payment method; rejection reason always given; shipping fees not refunded |
| `shipping-policy` | 7 | standard 3–7, express 1–3 business days; free shipping above ₹999; track with carrier + tracking number on the order; contact support if 10+ days past the estimated date; India only |
| `cancellation-policy` | 5 | cancel only while `placed`; after shipping no cancellation, return after delivery instead; cancelled orders refunded in 5–7 business days |
| `warranty-policy` | 7 | electronics 12 months, accessories 6, clothing none, from delivery; manufacturing defects; excludes physical and water damage; claim via support with order number; separate from the return window |
| `general-faq` | 8 | support Mon–Sat 9:00–19:00 IST; AI assistant 24/7, humans in support hours; meaning of placed/shipped/delivered/cancelled; cards, UPI, net banking, COD; contact = open a ticket and chat |

**Search check** (`python scripts/check_knowledge_search.py` on the host; logs in as agent1, `top_k=3`, maps `document_id` → slug via `GET /documents`): last run (2026-11-15) **rank 1: 10/10, top 3: 10/10**. Target: rank 1 ≥ 9/10, top 3 10/10. If results get worse, change the documents or chunking, never the questions.

**gRPC (M5).** Contracts in `/proto` (proto3; packages `order`, `knowledge`, `ticket`; unary RPCs only). Conventions: money is a string with two decimals (`"4500.00"`), dates are ISO-8601 strings (`"2026-11-15"`, timestamps `"2026-11-15T10:00:00.123+00:00"`), ids are UUID strings, `optional` fields may be absent (check with `HasField`). Each servicer method opens its own DB session, validates input (UUIDs, plus the **same Pydantic schema as the REST route**: `ReturnRequestCreate`, `MessageCreate`, `ApprovalCreate`, `SearchRequest`), calls the same service function as REST, converts the result, closes the session.

*OrderService* (`order:50051`). The caller supplies `customer_id` (required, valid UUID, else `INVALID_ARGUMENT`); every lookup is filtered by it exactly like the customer REST path, so another customer's order is `NOT_FOUND`.
| RPC | Request | Response |
|---|---|---|
| `ListOrders` | `customer_id` | `ListOrdersResponse{orders[]}` (by order number) |
| `GetOrder` | `order_number`, `customer_id` | `Order` |
| `CheckReturnEligibility` | `order_number`, `order_item_id`, `customer_id` | `ReturnEligibility`. Ineligible is a normal answer (`eligible=false` + reason code), not an error |
| `CreateReturnRequest` | `order_number`, `order_item_id`, `reason` (1–500), `customer_id` | `ReturnRequest`. Not eligible → `INVALID_ARGUMENT` (reason in details); already requested → `ALREADY_EXISTS`; unknown order/item → `NOT_FOUND` |
- `Order{order_number, customer_id, status, total_amount, ordered_at, items[], shipment}`; `shipment` is absent for placed/cancelled orders.
- `OrderItem{id, product_name, category, quantity (int32), unit_price}`.
- `Shipment{carrier, tracking_number, status, shipped_on, estimated_delivery, optional delivered_on}`.
- `ReturnEligibility{eligible, reason_code, reason, optional refund_amount, window_days, optional days_remaining}`: same reason codes as REST; `refund_amount` only when eligible; `window_days` is **0** where REST has null (no window applies); `days_remaining` absent where REST has null, 0 when expired.
- `ReturnRequest{id, order_number, order_item_id, status, refund_amount, reason, created_at}`.

*KnowledgeService* (`knowledge:50052`)
| RPC | Request | Response |
|---|---|---|
| `Search` | `query` (1–500), `top_k` (1–10; **0 = default 4**) | `SearchResponse{results[]}`, best first; `SearchResult{document_id, title, section, content, score (double)}`. Out-of-range input → `INVALID_ARGUMENT`; OpenAI failure → `INTERNAL` |

*TicketService* (`ticket:50053`). The caller is trusted with any ticket id (no customer filter, like the staff REST path); the ticket id is the agent's conversation thread.
| RPC | Request | Response |
|---|---|---|
| `GetTicket` | `ticket_id` | `Ticket{id, customer_id, customer_email, subject, status, optional assigned_agent_id, created_at, updated_at, messages[]}`, messages oldest first (the conversation history) |
| `PostMessage` | `ticket_id`, `sender_type` (**only `ai` or `system`**, else `INVALID_ARGUMENT`), `content` (1–5000) | `Message{id, sender_type, optional sender_id, content, created_at}`; `sender_id` always absent. Same `ticket_service.add_message` as REST (bumps `updated_at`, never reopens) |
| `CreateApproval` | `ticket_id`, `action_type` (**required**, `"refund"`), `return_request_id`, `amount` (string, >0, ≤2 decimals), `summary` (1–1000) | `Approval{id, ticket_id, action_type, return_request_id, amount, summary, status, auto_approved, created_at}`. Same `approval_service.create_approval` as REST: auto-approve limit, one approval per return request (`ALREADY_EXISTS`), pending → ticket `awaiting_approval`; unknown ticket `NOT_FOUND` |

*Error mapping* (one `abort_from_error` per service in `grpc_server.py`; REST mapping unchanged):
| Service error | gRPC status |
|---|---|
| `NotFoundError` | `NOT_FOUND` |
| `UnauthorizedError` | `UNAUTHENTICATED` |
| `ForbiddenError` | `PERMISSION_DENIED` |
| `ConflictError` | `ALREADY_EXISTS` |
| `BadRequestError`, `ValueError` (covers Pydantic `ValidationError` and a malformed UUID) | `INVALID_ARGUMENT` (message = the error text) |
| anything else | `INTERNAL` with `"Internal server error"`; traceback logged |

*Deadlines:* every client call sets a deadline (`timeout=` in Python). Convention: **5 s** default, **15 s** for `KnowledgeService.Search` (embedding call). The agent's clients (M7+) must follow it.

*Service tokens* (Part B):
- Every call sends metadata `authorization: Bearer <token>`. Token = JWT, **HS256**, signed with `SERVICE_TOKEN_SECRET` (shared, from `.env`), claims `sub` (calling service name), `iat`, `exp` = `iat + 60 s`. Callers mint a fresh token per call (or at least well within 60 s).
- `ServiceTokenInterceptor` (a `grpc.ServerInterceptor` in each `grpc_server.py`) checks it before any servicer code runs: missing, malformed, wrong signature, expired or missing claim → `UNAUTHENTICATED`; valid but `sub` not in `GRPC_ALLOWED_CALLERS` → `PERMISSION_DENIED`. It rejects by returning a unary handler that calls `context.abort`, and logs a warning.
- Allowlists (`GRPC_ALLOWED_CALLERS`, comma-separated, set literally in `docker-compose.yml`): order `agent`, knowledge `agent`, ticket `agent`.
- **Adding a caller:** give the calling service `SERVICE_TOKEN_SECRET` and have it sign with `sub=<its name>`; add that name to the target's `GRPC_ALLOWED_CALLERS` in `docker-compose.yml`; recreate the target container.
- Service identity only. End-user identity is the `customer_id` the trusted caller puts in the request; REST endpoints are unaffected (still RS256 user JWTs).

*Trust model:* gRPC callers are trusted to supply the right `customer_id` (and any `ticket_id`). The servers still enforce ownership for that customer, but they cannot know whether the caller picked the right customer. That is why only allowlisted services get in, and why in the agent (M7+) `customer_id`/`ticket_id` must be injected by code, never chosen by the model (`CLAUDE.md` section 9).

*Server lifecycle:* `start_grpc_server()` is called from the FastAPI `lifespan` in `main.py`: sync `grpc.server(ThreadPoolExecutor(max_workers=10), interceptors=[ServiceTokenInterceptor()])`, `add_insecure_port("0.0.0.0:<GRPC_PORT>")`, `start()` (non-blocking; gRPC serves on its own threads). Logs `gRPC server listening on port N (allowed callers: ...)`. `stop_grpc_server()` on shutdown (`stop(grace=5)`). No reflection, health service, streaming or TLS.

*Code generation:* `python scripts/generate_proto.py` (host, from the repo root) runs `grpc_tools.protoc` per target: each service gets only its own proto in `app/generated/`, `scripts/generated/` gets all three; it writes an `__init__.py` and rewrites `import x_pb2 as ...` in `*_pb2_grpc.py` to `from . import x_pb2 as ...` (the absolute import breaks inside a package). Generated code is committed; regenerating with the same versions is byte-identical. **Pins must match:** grpcio-tools 1.85.1 bundles protoc 35.1; its `*_pb2.py` refuses to load on protobuf < 7.35.1 and its `*_pb2_grpc.py` on grpcio < 1.85.1. Services pin `grpcio==1.85.1`, `protobuf==7.35.1`. Upgrade all three together and regenerate.

**Events (M6).** RabbitMQ topic exchange **`support.events`** (durable). Routing key = `event_type`. Messages are persistent (`delivery_mode=2`), `content_type: application/json`, `message_id` = `event_id`. Envelope:
`{"event_id": "<uuid4>", "event_type": "ticket.created", "occurred_at": "2026-11-15T10:00:00.123456+00:00", "data": {...}}`. All ids in `data` are UUID strings. Events carry IDs and a few small fields, **never message text, amounts or full objects**.

| Event (= routing key) | Published when (by `ticket_service` / `approval_service`, after the commit) | `data` fields | Consumers |
|---|---|---|---|
| `ticket.created` | a ticket is created (REST) | `ticket_id`, `customer_id` | realtime |
| `ticket.message_created` | any message is added: ticket creation (first message), REST `POST .../messages`, gRPC `PostMessage` | `ticket_id`, `customer_id`, `message_id`, `sender_type` | realtime (agent from M7) |
| `ticket.status_changed` | every real status change: staff `PATCH .../status` (not when setting the same status), customer message reopening a `resolved` ticket, pending approval → `awaiting_approval`, decision → back to `open` | `ticket_id`, `customer_id`, `old_status`, `new_status` | realtime |
| `ticket.assigned` | `POST .../assign` | `ticket_id`, `customer_id`, `assigned_agent_id` | realtime |
| `approval.requested` | an approval is created as `pending` (REST or gRPC) | `approval_id`, `ticket_id`, `customer_id`, `return_request_id`, `action_type` | realtime (staff only) |
| `approval.decided` | approve, reject, **or auto-approval at creation** | same as above + `status` (`approved`/`rejected`), `auto_approved` (bool) | realtime (staff only; order from M9) |
Order of events for one action: create ticket → `ticket.created`, `ticket.message_created`; customer message on a resolved ticket → `ticket.message_created`, `ticket.status_changed`; pending approval → `approval.requested`, `ticket.status_changed` (only if the ticket was not already `awaiting_approval`); auto-approval → `approval.decided` only; decision → `approval.decided`, `ticket.status_changed` (only if the ticket was `awaiting_approval`). The approval amount is not in the events: in M9 Order fetches the approval over gRPC.

*Publishing* (`services/ticket/app/events.py`, `publish_event(event_type, data)`): builds the envelope, opens a **new `pika.BlockingConnection` per publish** (pika is not thread-safe; REST and gRPC run on several threads) with `connection_attempts=1` and 3 s `socket_timeout`/`stack_timeout`/`blocked_connection_timeout`, declares the exchange, publishes, closes. Any error is logged (`Could not publish event ...`) and swallowed: the request still succeeds and the event is lost. Routers and the gRPC servicer were not changed; both reach the same service functions.

*Realtime consumption and fan-out:*
1. Every instance consumes from the **same durable queue `realtime.events`**, bound to `support.events` with `#`, manual ack, `prefetch_count=1` (RabbitMQ hands events out in turn; each goes to exactly one instance). `aio_pika.connect_robust` reconnects and restores the consumer after a drop; a retry loop (2 s) covers only the first connect.
2. The receiving instance publishes the raw message body to the **Redis pub/sub channel `realtime:events`**, then acks. If the Redis publish fails it waits 2 s and `nack(requeue=True)`s (redelivered, maybe to another instance). A body that is not a JSON event is logged and acked (dropped).
3. **Every** instance subscribes to `realtime:events` (retry loop with a 2 s sleep after any error) and delivers to its own local sockets by the routing rules.

*Rooms and routing* (in-memory per instance, `connections.py`): every authenticated socket is in `user:<user_id>`; `agent`/`admin` sockets are also in `staff`. `ticket.*` → `staff` + `user:<data.customer_id>`; `approval.*` → `staff` only; any other type → `staff` only. A socket in two matching rooms gets the event once. No per-ticket rooms: browsers filter by `ticket_id` themselves. A socket whose send fails or takes over 5 s is removed from its rooms.

*WebSocket protocol* (`ws://localhost:8080/ws/connect`; Nginx strips `/ws`, FastAPI path `/connect`):
| Direction | Message |
|---|---|
| client → server (first, within **5 s**) | `{"type": "auth", "token": "<jwt>"}`; token verified locally (RS256, public key, expiry, required `sub`, `email`, `role`, `exp`) |
| server → client | `{"type": "auth_ok", "user_id": "...", "role": "...", "instance": "<container hostname>"}` |
| server closes | code **4401** (reason `Unauthorized`) on timeout, non-JSON, a first message that is not `auth`, or an invalid/expired token |
| client → server | `{"type": "ping"}` → `{"type": "pong"}`; anything else → `{"type": "error", "message": "Unknown message type"}` (or `"Messages must be JSON"`) |
| server → client | `{"type": "event", "event": <envelope>}` |
Delivery is **at-least-once** (a RabbitMQ redelivery can repeat an event): clients treat events as "refresh" signals and may deduplicate by `event_id`. Realtime REST: only `GET /health` (also returns `instance`). Logs (`docker compose logs realtime`) show `[<instance>] connected / disconnected / forwarded ... to Redis / delivered ... to N local connection(s)`.

*Test page* (`frontend/test/realtime.html`, **throwaway**, delete once the real frontends exist): http://localhost:8080/test/realtime.html. Log in (prefilled customer1), Connect, "Connect with a bad token" (closes 4401), Send ping, status line with `auth_ok` and the instance id, event log (newest first) with type, `event_id` and `data`; on `ticket.message_created` it fetches the ticket over REST with the tab's token and lists its messages; a repeated `event_id` is shown as **DUPLICATE** in red. Token per tab in `sessionStorage`.

**Celery tasks:** `app.tasks.ingest_document` (knowledge, above). Events never use Celery.

**Environment variables per service** (set in `docker-compose.yml`, values from `.env`):
`SERVICE_NAME`, `PORT`, `ROOT_PATH` (the Nginx prefix), `DATABASE_URL` (not for realtime), `REDIS_URL`, `RABBITMQ_URL`. Identity also gets `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`, `ACCESS_TOKEN_EXPIRE_MINUTES`. Order also gets `JWT_PUBLIC_KEY_PATH` (and mounts only `keys/public`). Ticket gets `JWT_PUBLIC_KEY_PATH` and `REFUND_AUTO_APPROVE_LIMIT` (mounts only `keys/public`). Knowledge gets `JWT_PUBLIC_KEY_PATH`, `OPENAI_API_KEY`, `EMBEDDING_MODEL` (mounts only `keys/public`). Realtime (M6) gets `JWT_PUBLIC_KEY_PATH` (mounts only `keys/public`), no `DATABASE_URL`; it has no `container_name` and no host port so it can be scaled. `knowledge-worker` gets the same variables as knowledge minus `PORT`/`ROOT_PATH` (`SERVICE_NAME=knowledge-worker`) and mounts no keys (it never checks tokens; the path is only required by `config.py`).
M5: order, knowledge and ticket also get `GRPC_PORT` (50051 / 50052 / 50053), `SERVICE_TOKEN_SECRET` (`${SERVICE_TOKEN_SECRET:?...}`: Compose refuses to start if it is unset or empty) and `GRPC_ALLOWED_CALLERS` (`agent`). The worker gets `GRPC_PORT: 50052`, `SERVICE_TOKEN_SECRET: not-used-by-the-worker` and `GRPC_ALLOWED_CALLERS: none` only because it shares `config.py`; it never starts a gRPC server and never sees the real secret.
`.env` also holds the three JWT settings above (paths are container paths), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `<SERVICE>_DATABASE_URL` ×5, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `OPENAI_API_KEY`, `EMBEDDING_MODEL` (knowledge + worker), `CHAT_MODEL` (not passed to any service yet), `REFUND_AUTO_APPROVE_LIMIT` (ticket), `SERVICE_TOKEN_SECRET` (M5; 64 hex characters from `secrets.token_hex(32)`; `.env.example` has a placeholder).

## 8. Milestone status

- [x] M0 Skeleton and infrastructure
- [x] M1 Identity
- [x] M2 Order
- [x] M3 Ticket
- [x] M4 Knowledge
- [x] M5 gRPC layer
- [x] M6 Events and Realtime
- [ ] M7 Agent RAG
- [ ] M8 Agent order tools
- [ ] M9 Approval and refund flow
- [ ] M10 Human takeover and AI activity log
- [ ] M11 Customer app
- [ ] M12 Agent dashboard
- [ ] M13 Hardening and demo

**Demo orders** (`docker compose exec order python seed.py`, safe to run again; dates are relative to the day the seed runs; every item has quantity 1; amounts in INR; later milestones depend on these numbers):
| Order | Customer | Status | Items (category, price) | Scenario |
|---|---|---|---|---|
| ORD-1001 | customer1 | delivered 5 days ago | Wireless headphones (electronics, 4500), Phone case (accessories, 800) | eligible; headphones refund 4500 is under the 5000 auto-approve limit |
| ORD-1002 | customer1 | delivered 40 days ago | T-shirt (clothing, 1200) | `window_expired` |
| ORD-1003 | customer1 | shipped, in transit (Delhivery `DL-1003`) | Backpack (accessories, 2200) | `not_delivered`, live tracking |
| ORD-1004 | customer2 | delivered 10 days ago | Smartwatch (electronics, 18000) | eligible; large refund that needs human approval |
| ORD-1005 | customer2 | delivered 3 days ago | Gift card (gift_card, 2000) | `non_returnable` |
| ORD-1006 | customer2 | delivered 20 days ago | Bluetooth speaker (electronics, 3500) | `window_expired` (15-day electronics window) |
| ORD-1007 | customer2 | delivered 7 days ago | Jacket (clothing, 2500) | `already_requested` (seeded return request, status `requested`) |
| ORD-1008 | customer3 | delivered 12 days ago | Running shoes (clothing, 3000), Socks (clothing, 400) | eligible; small refund |
| ORD-1009 | customer3 | placed, not shipped | Desk lamp (electronics, 1500) | `not_delivered`, no shipment |
| ORD-1010 | customer3 | cancelled | Notebook set (accessories, 600) | cancelled order, no shipment |
Delivered orders have a `BlueDart` shipment (`BD-<number>`, shipped 3 days before delivery, ordered 5 days before delivery). Customer ids are the fixed ids from the Identity seed.

## 9. Design decisions

- Repo root is this folder (`ecom4/`), standing in for `ai-support-platform/`; the Compose project is named `ai-support-platform`.
- Nginx strips the route prefix; each FastAPI app gets `ROOT_PATH` so Swagger `/docs` works through Nginx.
- `/ws/` also strips its prefix: the realtime endpoint `/connect` is reached at `ws://localhost:8080/ws/connect`; realtime health is at `/ws/health`. Nginx keeps idle WebSockets for 24 h (`proxy_read_timeout`/`proxy_send_timeout 86400s`).
- One identical Dockerfile; the port comes from the `PORT` env var. It copies the whole service folder so later files (alembic) need no Dockerfile change.
- Database URLs use the SQLAlchemy psycopg 3 form `postgresql+psycopg://...` and are full values in `.env` (user/password repeated).
- Postgres healthcheck uses `pg_isready -h 127.0.0.1` (TCP), so it only turns healthy after the init script has created the databases.
- RabbitMQ uses its own user from `.env` (the default `guest` cannot log in from other containers); the same login opens the management UI.
- All five API services (and realtime, minus Postgres) wait for Postgres, Redis and RabbitMQ healthy, so later milestones need no `depends_on` changes.
- Service containers have no Docker healthcheck (`python:3.12-slim` has no curl); Nginx just starts after them.
- No code bind mounts: code changes need `docker compose up --build`.
- Placeholder pages: the customer page checks `/api/identity/health`, the agent page checks `/api/tickets/health`.
- Identity migrations run from the `command:` in `docker-compose.yml` (`alembic upgrade head && exec uvicorn ...`, with `$$PORT` so Compose does not substitute it); the Dockerfile stays identical.
- `alembic/env.py` is online-mode only (no offline SQL generation), to keep it short.
- Swagger through Nginx needed no extra work: the M0 `root_path` setup is enough (`/api/identity/docs` loads and "Authorize" accepts a pasted token via `HTTPBearer`).
- Emails are lowercased on register and login, so duplicates are caught regardless of case.
- Login takes a JSON body (not an OAuth2 form); Swagger's Authorize button is a paste-the-token box.
- `users.role` has a database check constraint in addition to the code-level rule (self-registration always creates `customer`).
- The user repository's `create` commits and takes an optional `user_id`, used only by the seed script for the fixed demo ids.
- `generate_keys.py` refuses to overwrite existing keys (replacing them would invalidate every token) and sets the private key to mode 600.
- `jwt.decode` requires `sub`, `email`, `role` and `exp`, and only accepts `RS256`; tokens signed with another key or `alg: none` return 401.
- Order repositories only `flush`; order services own the `commit`. This lets `execute_refund` write the refund and the `refunded` status in one transaction (Identity's repository commits itself; there nothing needed two writes).
- Order and return lookups for customers filter by `customer_id` in the query itself, so another customer's data is a plain 404 and existence is never revealed. Staff pass no filter.
- A role branch (`customer` → own id, otherwise no filter) is written inline in the three routes that allow staff, instead of a helper.
- `GET /orders` and `/returns` list endpoints are customer-only (agents/admins get 403); staff open single records by order number or return id.
- Same `approval_id` sent for a different return request returns 409 (it would otherwise hit the unique constraint); not specified in the milestone prompt.
- Money is `Decimal` in the database and services; `schemas.py` has one small `Money` type that serializes it as a JSON number with two decimals (Pydantic would otherwise send a string).
- Order items are returned sorted by product name; all order-list queries load items and shipment with `selectinload`.
- The order seed uses the models directly (one commit per order), not repositories, because it builds whole orders with items and shipment in one go.
- `UnauthorizedError` was added as a fifth error class (also now listed in `CLAUDE.md` section 5).
- Ticket service follows the order pattern: repositories only `flush`, services `commit`; `auth.py`, `errors.py`, `database.py`, alembic files and the `Money` type copied from order.
- Ticket detail and create responses are `{ticket, messages}` (two keys) instead of a flat ticket with nested messages: the service returns the ticket plus `message_repository.list_by_ticket`, and there is no ORM relationship on `Ticket`.
- The create-ticket body field for the first message is `message`; posting a message uses `content`.
- Customers may also use the `status` filter on `GET /tickets` (it only narrows their own tickets).
- An admin posting a message is stored as `sender_type: agent` with the admin's id as `sender_id`.
- `updated_at` is set with SQL `now()` (transaction time) in the ticket repository; the decision time `decided_at` is computed in the service (UTC).
- `action_type` defaults to `refund` and is the only accepted value; amounts must have at most 2 decimals and 10 digits (fits `Numeric(10,2)`). Summary and notes are 1–1000 characters, trimmed.
- On a decision the ticket goes back to `open` only if it is `awaiting_approval` (a ticket staff already escalated or resolved keeps that status).
- The ticket seed builds rows with the models directly (like the order seed) so it can set fixed ids and realistic message times.
- Knowledge follows the order/ticket pattern (repositories flush, services commit); `auth.py`, `errors.py`, `database.py` and the alembic files copied from ticket.
- PGVector gets the service's existing SQLAlchemy engine (built from the psycopg 3 URL) instead of the URL string, so it does not create a new connection pool on every call.
- The knowledge API calls `rag.open_vector_store()` at startup to create PGVector's tables and collection once; otherwise the worker's parallel processes race to create them on the first ingestions.
- `rag.open_vector_store()` is a small shared function (used by the three RAG entry points and startup); it builds the store on each call instead of caching it.
- Score = 1 − cosine distance (cosine similarity), rounded to 4 decimals.
- Text before the first H2 gets section `Overview`. A document that produces no chunks fails with "The document has no text to index".
- On failure `chunk_count` is set to 0, because rag.py deletes the old chunks before embedding the new ones (most failures happen at the embedding call).
- Re-ingest is allowed in any status (also `processing`): it is idempotent, and it is the only way to recover a document stuck in `pending`/`processing`.
- `GET /documents` omits `content`; the single-document, create and re-ingest responses include it.
- Search responses are wrapped as `{results: [...]}` (room for more fields later; matches a `repeated` field in the M5 `.proto`).
- The seed script calls `document_service.create_document` (the same code as the API, so it also queues the ingestion); the title is the file's H1 line.
- `search_service` re-checks query length and `top_k` (BadRequestError, 400) as a backstop; REST (422) and gRPC (`SearchRequest` schema, `INVALID_ARGUMENT`) validate before that.
- gRPC versions: grpcio 1.85.1 + grpcio-tools 1.85.1 + protobuf 7.35.1 (protobuf pinned to the exact protoc 35.1 gencode version, the oldest runtime the generated code accepts).
- The gRPC server is started from a FastAPI `lifespan` (an async context manager is what Starlette requires; the calls inside are sync). `server.start()` already serves on gRPC's own thread pool, so no extra `threading.Thread` is needed.
- `abort_from_error` maps any `ValueError` to `INVALID_ARGUMENT`: that covers Pydantic `ValidationError` and `uuid.UUID(...)` failures without a separate UUID check in every method.
- Order conversion to proto is written out in both `ListOrders` and `GetOrder` (no `order_to_message` helper), because the milestone allows `abort_from_error` as the only shared helper.
- `ReturnEligibility.window_days` is a plain `int32` (0 = no window applies) as the milestone listed; `refund_amount` and `days_remaining` are `optional`.
- `GetTicket` returns the messages inside `Ticket` (`repeated Message messages`), instead of REST's `{ticket, messages}` wrapper.
- `ReturnRequest.order_number` comes from the validated request (the row stores `order_id`); `Message` over gRPC has no `ticket_id` field (it is the request's ticket).
- `CreateApproval.action_type` is required over gRPC (an empty string fails `ApprovalCreate`'s `Literal["refund"]`), unlike the REST default.
- `KnowledgeService.Search` opens no DB session in the servicer: `search_service` → `rag.py` uses the engine directly.
- `SearchResult.score` is a `double` (a 32-bit `float` would turn 0.6123 into 0.61230000257).
- Service tokens: missing secret is caught by Compose's `${SERVICE_TOKEN_SECRET:?...}` rather than by code. The allowlists are literal values in `docker-compose.yml` (like `PORT`), not `.env` entries.
- Events are notification-only (IDs and a few small fields, no content); browsers refetch over REST, so authorization stays in one place (the owning service).
- No per-ticket rooms or subscriptions: only `user:<id>` and `staff`; browsers filter events by `ticket_id` themselves.
- Multi-instance Realtime: one shared RabbitMQ queue (competing consumers) + Redis pub/sub fan-out, so an event is consumed once and delivered once per connection whichever instance holds it.
- Publishing lives in the service layer after each commit (`publish_event`), so REST and gRPC both publish without changes to routers or the servicer.
- `ticket.status_changed` is published only when the status really changes (staff setting the current status again publishes nothing; a second pending approval on an `awaiting_approval` ticket publishes only `approval.requested`).
- Envelope UUIDs are serialized with `json.dumps(default=str)`; the services pass model attributes straight in. Event data is read from the models after the commit (SQLAlchemy reloads them), the old status is captured before the change.
- Realtime consumer uses `prefetch_count=1` (simple, ordered per instance, fair round robin between instances); throughput is far beyond what the demo needs.
- Realtime forwards the raw RabbitMQ body to Redis unchanged; it only parses it to reject non-JSON messages (acked and dropped, so a poison message cannot loop forever).
- Realtime auth failures (timeout, bad JSON, wrong first message, bad token) all close with 4401; the server accepts the socket first, because a close code can only be sent on an accepted WebSocket.
- Instance id = container hostname (Docker sets it to the short container id), also returned by `/ws/health` so Nginx's round robin is visible.
- The test page is one self-contained HTML file with inline CSS and JS (allowed for a throwaway page); Nginx has a `/test/` location for it.
- `grpc_client_check.py` reads `SERVICE_TOKEN_SECRET` by scanning the repo's `.env` (stdlib only) and mints a fresh token per call with PyJWT; it exits with status 1 if any step fails.

## 10. How to run

```bash
cp .env.example .env          # first time only
docker compose up --build     # add -d to run in the background
docker compose down -v        # stop and wipe databases (re-runs the init script next start)
```
```bash
python scripts/generate_keys.py                  # once, BEFORE the first `docker compose up` (needs `pip install cryptography` on the host)
docker compose exec identity python seed.py      # demo users; safe to run again
```
```bash
docker compose exec order python seed.py         # ten demo orders for the fixed demo customer ids; safe to run again
```
```bash
docker compose exec ticket python seed.py        # three demo tickets with messages; safe to run again
```
```bash
docker compose exec knowledge python seed.py     # six policy documents, ingested by the worker; safe to run again (no re-embedding)
docker compose logs -f knowledge-worker          # watch the ingestion
python scripts/check_knowledge_search.py         # on the host, after the documents are ready (standard library only)
```
```bash
# M5 gRPC, on the host (a venv is easiest: python3 -m venv .venv && source .venv/bin/activate)
pip install grpcio-tools==1.85.1 grpcio==1.85.1 protobuf==7.35.1 PyJWT==2.15.1
python scripts/generate_proto.py                 # only after changing a .proto (generated code is committed)
python scripts/grpc_client_check.py              # needs the stack up and the identity, order, ticket and knowledge seeds
```
```bash
# M6 events and realtime
docker compose up --build --scale realtime=2     # two Realtime instances; scale on `up` (else `docker compose restart nginx`)
# then open http://localhost:8080/test/realtime.html in several tabs (one user per tab)
# RabbitMQ UI http://localhost:15672: exchange support.events, queue realtime.events with 2 consumers
```
`.env` needs `SERVICE_TOKEN_SECRET` (M5) and a real `OPENAI_API_KEY` for ingestion and search. Each service's tables are created by its container's `alembic upgrade head` on startup (PGVector's tables by the knowledge API at startup).

## 11. Known limitations and deviations

- `scripts/init_databases.sql` runs only when the Postgres volume is new; after changing it, run `docker compose down -v`.
- Nginx resolves service hostnames once at startup. If you recreate a single service container (`docker compose up -d --build order`), run `docker compose restart nginx` if you see 502 errors.
- Events are lost if RabbitMQ is down at publish time (no outbox). The request still succeeds; the ticket log shows `Could not publish event ...`.
- Event delivery is at-least-once (a RabbitMQ redelivery, e.g. after an ack is lost, repeats an event); clients must tolerate duplicates (dedupe by `event_id`).
- A token that expires while a WebSocket stays open is not rechecked; the connection keeps receiving events until it closes.
- A new RabbitMQ connection is opened for every publish (simple and thread-safe, but slower than a pooled connection; a handful of events per request is fine).
- Redis pub/sub has no buffer: an instance whose Redis subscription is down (or reconnecting) misses the events published meanwhile, and its clients do not get them. Browsers should refetch after reconnecting.
- If Redis is down, events stay in the RabbitMQ queue (requeued every 2 s) and are delivered when Redis is back; after a Redis restart the first forward on a stale connection fails once and is requeued.
- Nginx resolves the `realtime` replicas only at startup: after `docker compose up --scale realtime=N` on a running stack, restart Nginx. A stopped replica keeps receiving new connection attempts until then (Nginx retries the next one).
- Rooms are in memory per instance; restarting an instance drops its WebSockets (clients must reconnect).
- One slow client can delay delivery to others on the same instance by up to 5 s per event (sends are sequential, with a 5 s timeout).
- Events are published after the commit but outside the transaction: a crash between commit and publish loses the event.
- Realtime also logs FastAPI's `root_path` deprecation warning (same as the other services).
- `frontend/test/realtime.html` is a throwaway verification page; delete it and Nginx's `/test/` location when M11/M12 exist.
- Deviation: the ticket seed script is `services/ticket/seed.py`, for the same reason as identity's and order's.
- Deviation: the order seed script is `services/order/seed.py` for the same reason as identity's (it needs the service's models). Both are copied into the image: rebuild after editing them.
- Deviation: the identity seed script is `services/identity/seed.py`, not in `/scripts`, because it needs the service's models, repository and password hashing (the file is copied into the image with the rest of the service folder; rebuild after editing it).
- The order seed's dates are fixed when it first runs; after a few days the demo orders age (for example ORD-1001 will leave the electronics window). Run `docker compose down -v` and re-seed to reset.
- Return requests can only be created, never rejected or cancelled, and refunds cannot be reversed (not in the milestone).
- If `keys/` is missing when Compose starts, Docker creates empty root-owned `keys/private` and `keys/public` folders and identity stops with the "key not found" message; delete `keys/`, run `generate_keys.py`, and start again.
- Login does not hide whether an email exists through timing (an unknown email returns slightly faster than a wrong password). Acceptable for this learning project.
- Two simultaneous registrations of the same new email can make the loser get a 500 instead of 409 (the unique index still prevents a duplicate).
- FastAPI logs a deprecation warning about the `root_path` constructor argument (from M0); it still works. Fix later by passing `--root-path` to Uvicorn.
- Deciding one approval moves an `awaiting_approval` ticket back to `open` even if another approval on the same ticket is still pending (rare; the remaining one still shows in `GET /approvals?status=pending`).
- Staff can set a ticket that is `awaiting_approval` to `open`/`escalated`/`resolved` by hand; the pending approval stays pending.
- Knowledge: if enqueueing fails after the document is saved (for example RabbitMQ is down), the API returns 500 and the document stays `pending` until it is re-ingested.
- Knowledge: a document deleted while its ingestion is running can leave orphaned chunks (the worker stores them after the delete). Re-create the slug and delete it again, or clean up by `cmetadata->>'document_id'`.
- Knowledge: if an ingestion fails before the old chunks are deleted (e.g. a database error), `chunk_count` shows 0 while old chunks still exist; the next successful ingestion overwrites the same ids, so nothing is duplicated.
- Knowledge: the vector column is fixed at 1536 dimensions; changing `EMBEDDING_MODEL` to a model with another size needs a new collection and table (`docker compose down -v`), and re-ingesting.
- Knowledge: retries are not selective; permanent errors (bad API key, empty document) are also retried 3 times before ending `failed`.
- Knowledge: an OpenAI error during `/search` returns a plain 500.
- Celery runs as root in the container and prints a security warning at startup (harmless here; JSON-only serialization).
- Message ordering uses `created_at` (transaction time) with `id` as a tie-breaker; two messages in one transaction would have the same timestamp (does not happen through the API).
- gRPC service tokens use one **shared HS256 secret**: any service holding it could mint a token for any caller name (`sub`), so the allowlist only protects against services that do not have the secret. Asymmetric keys per service would fix this; out of scope.
- gRPC is plaintext (`add_insecure_port`, no TLS) inside the Docker network; tokens are only protected by the network being private.
- The gRPC ports are published on the host's `127.0.0.1` for the check script (development only); remove the `ports:` entries in a real deployment.
- Every server checks `exp` with no clock-skew leeway (all containers share the host clock).
- gRPC `INVALID_ARGUMENT` details for schema errors are Pydantic's full multi-line message (including a docs URL).
- The `grpc_client_check.py` deadline step assumes `ListOrders` takes longer than 1 ms end to end (true in practice: it runs SQL queries).
- Each `grpc_client_check.py` run creates a new ticket for customer1 with two approvals (one pending), which stay in the demo data.

## 12. Open items for later milestones

- `return_request_id` and `amount` on an approval are supplied by the caller and are **not verified** against the Order service (M3, still true after M5's `CreateApproval`). In M9 the Order service must verify the approval (status, return request ID, amount) with the Ticket service over gRPC before executing a refund.
- M9: add `GetApproval` to `TicketService` (`ticket.proto`, regenerate) and add `order` to the Ticket server's `GRPC_ALLOWED_CALLERS` (`agent,order`); the order service then needs `SERVICE_TOKEN_SECRET` as a caller.
- M7/M8: the agent service needs `SERVICE_TOKEN_SECRET`, `grpcio`/`protobuf` at the same pins, its own generated stubs (add `services/agent/app/generated` to `TARGETS` in `generate_proto.py`) and the deadline convention.
- `assigned_agent_id` is not validated against the Identity service (it is always the caller's own id from the token today).
- `customer_email` on a ticket is a copy from the token; it is not updated if the user's email changes.
- M10: a takeover flag on tickets (the AI must not reply when a human has taken over).
- M7: the agent consumes `ticket.message_created` with **its own** durable queue (e.g. `agent.ticket_messages`, bound to `ticket.message_created`), not `realtime.events`; it must ignore messages whose `sender_type` is not `customer` (its own `ai` replies also produce this event) and be safe to run twice.
- M9: Order consumes `approval.decided` with its own queue and deduplicates on `approval_id` (the refunds unique constraint already guarantees idempotency).
