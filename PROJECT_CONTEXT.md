# PROJECT_CONTEXT.md

Living source of truth. Updated at the end of every milestone. Rules live in `CLAUDE.md`.

## 1. Overview

An AI Customer Support Automation Platform for a simulated e-commerce company, built as a learning project. Customers open tickets and chat with an AI assistant (RAG + tool calling) that can look up orders and request returns or refunds; sensitive actions need human approval in an agent dashboard. Orders and refunds are mock data.

## 2. Architecture and communication

- Browser → Nginx (`localhost:8080`) → services over REST. Nginx strips the prefix (`/api/orders/health` reaches the order service as `/health`).
- WebSockets: `/ws/<path>` → realtime service as `/<path>`, with upgrade headers.
- Planned (not built yet): gRPC for synchronous service-to-service calls (M5), RabbitMQ topic exchange `support.events` for events (M6), Celery for background jobs, Redis for Celery results, per-ticket locks and WebSocket fan-out.
- Auth: Identity signs RS256 JWTs with a private key; every other service verifies them locally with the public key (no call to Identity).
- Current state (M3): Identity, Order and Ticket have databases and real endpoints (REST only, no calls between services). Knowledge, agent and realtime only expose `GET /health`. Nothing uses Redis or RabbitMQ yet; the containers just wait for them to be healthy.

## 3. Tech stack

Python 3.12, FastAPI 0.162.7, Uvicorn 0.54.0, pydantic-settings 2.16.3. Postgres 17 + pgvector 0.8.2 (`pgvector/pgvector:0.8.2-pg17`), Redis 7.4 (`redis:7.4-alpine`), RabbitMQ 4.2 (`rabbitmq:4.2-management-alpine`), Nginx 1.28 (`nginx:1.28-alpine`). Docker Compose. Plain HTML/CSS/JS frontends.
Identity (M1) also uses SQLAlchemy 2.1.8 (sync), psycopg 3.3.9 (binary), Alembic 1.20.1, PyJWT 2.15.1, cryptography 50.0.2, argon2-cffi 25.1.0, email-validator 2.3.0.
Order (M2) and Ticket (M3) use the same pinned SQLAlchemy, psycopg, Alembic, PyJWT and cryptography versions as Identity.
Later milestones add: Celery, pika/aio-pika, grpcio, LangChain/LangGraph (see `CLAUDE.md` section 7).

## 4. Services

| Service | Purpose | REST port | gRPC port | Database | Nginx prefix |
|---|---|---|---|---|---|
| identity | Users, login, JWT issuing | 8001 | – | identity_db | `/api/identity/` |
| ticket | Tickets, messages, approvals | 8002 | 50053 (M5) | ticket_db | `/api/tickets/` |
| order | Mock orders, returns, simulated refunds | 8003 | 50051 (M5) | order_db | `/api/orders/` |
| knowledge | Policy documents, RAG search | 8004 | 50052 (M5) | knowledge_db (pgvector) | `/api/knowledge/` |
| agent | LangGraph AI agent | 8005 | – | agent_db | `/api/agent/` |
| realtime | WebSocket push to browsers | 8006 | – | none | `/ws/` |

Published to the host: Nginx `8080`, RabbitMQ management UI `15672`. Service ports, Postgres, Redis and AMQP are internal only.

## 5. Repository structure

```
.
├── CLAUDE.md, PROJECT_CONTEXT.md, README.md
├── docker-compose.yml, .env.example, .env (not committed), .gitignore
├── keys/                      # not committed: private/private.pem, public/public.pem
├── nginx/nginx.conf
├── proto/                     # .gitkeep only (M5)
├── scripts/
│   ├── init_databases.sql     # Postgres init: 5 databases + vector extension
│   └── generate_keys.py       # creates the RSA key pair in keys/
├── frontend/
│   ├── customer/  index.html, app.js (placeholder health check)
│   └── agent/     index.html, app.js (placeholder health check)
└── services/<identity|ticket|order|knowledge|agent|realtime>/
    ├── Dockerfile             # identical in all six
    ├── requirements.txt       # M0 packages only, except identity, order, ticket (database + JWT packages added)
    └── app/
        ├── main.py            # FastAPI app + GET /health
        └── config.py          # pydantic-settings

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

Databases created by `scripts/init_databases.sql`: `identity_db`, `ticket_db`, `order_db`, `knowledge_db` (with `vector` extension), `agent_db`. `identity_db`, `order_db` and `ticket_db` have tables so far.

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

**Return eligibility result** (the gRPC contract in M5 copies this exact shape): `eligible` (bool), `reason_code` (`eligible` | `not_delivered` | `non_returnable` | `window_expired` | `already_requested`), `reason` (sentence), `refund_amount` (quantity × unit price, only when eligible, else null), `window_days` and `days_remaining` (set once the order is delivered and the category is returnable, else null; `days_remaining` is 0 when expired). Rules, checked in this order (constants at the top of `return_service.py`): item belongs to the order (else 404) → order is `delivered` (with a shipment delivery date) → category is not `gift_card`/`perishable` → `days_since_delivery <= window` (30 days, **15 for `electronics`**) → no existing return request for the item. Decided per whole order line, no partial quantities. `create_return_request` runs the same check again and never trusts an earlier one.

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
The REST API can never create `ai` or `system` messages. `ticket_service.add_message` takes `sender_type`/`sender_id` as parameters so the gRPC layer can post `ai`/`system` messages later.

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

**gRPC:** none yet. **Events:** none yet. **Celery tasks:** none yet.

**Environment variables per service** (set in `docker-compose.yml`, values from `.env`):
`SERVICE_NAME`, `PORT`, `ROOT_PATH` (the Nginx prefix), `DATABASE_URL` (not for realtime), `REDIS_URL`, `RABBITMQ_URL`. Identity also gets `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`, `ACCESS_TOKEN_EXPIRE_MINUTES`. Order also gets `JWT_PUBLIC_KEY_PATH` (and mounts only `keys/public`). Ticket gets `JWT_PUBLIC_KEY_PATH` and `REFUND_AUTO_APPROVE_LIMIT` (mounts only `keys/public`).
`.env` also holds the three JWT settings above (paths are container paths), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `<SERVICE>_DATABASE_URL` ×5, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL` (not passed to any service yet), `REFUND_AUTO_APPROVE_LIMIT` (ticket).

## 8. Milestone status

- [x] M0 Skeleton and infrastructure
- [x] M1 Identity
- [x] M2 Order
- [x] M3 Ticket
- [ ] M4 Knowledge
- [ ] M5 gRPC layer
- [ ] M6 Events and Realtime
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
- `/ws/` also strips its prefix: a future endpoint `/tickets/{id}` in realtime is reached at `ws://localhost:8080/ws/tickets/{id}`; realtime health is at `/ws/health`.
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
Seed data for policies arrives with M4. Each service's tables are created by its container's `alembic upgrade head` on startup.

## 11. Known limitations and deviations

- `scripts/init_databases.sql` runs only when the Postgres volume is new; after changing it, run `docker compose down -v`.
- Nginx resolves service hostnames once at startup. If you recreate a single service container (`docker compose up -d --build order`), run `docker compose restart nginx` if you see 502 errors.
- Realtime has no WebSocket library yet (Uvicorn logs "No supported WebSocket library"); M6 adds one.
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
- Message ordering uses `created_at` (transaction time) with `id` as a tie-breaker; two messages in one transaction would have the same timestamp (does not happen through the API).

## 12. Open items for later milestones

- `return_request_id` and `amount` on an approval are supplied by the caller and are **not verified** against the Order service in M3. In M9 the Order service must verify the approval (status, return request ID, amount) with the Ticket service over gRPC before executing a refund.
- `assigned_agent_id` is not validated against the Identity service (it is always the caller's own id from the token today).
- `customer_email` on a ticket is a copy from the token; it is not updated if the user's email changes.
- M10: a takeover flag on tickets (the AI must not reply when a human has taken over).
