# AI Customer Support Automation Platform

A learning project: an AI customer support platform for a simulated e-commerce company, built as six FastAPI microservices behind Nginx. See `PROJECT_CONTEXT.md` for the current state and `CLAUDE.md` for the project rules.

## Start

```bash
cp .env.example .env              # first time only, then edit values
python scripts/generate_keys.py   # first time only: creates the JWT keys in keys/ (needs `pip install cryptography`)
docker compose up --build
docker compose exec identity python seed.py   # demo users (password: Password123!)
```

`generate_keys.py` must be run once **before** `docker compose up`, otherwise the identity service stops with a "key not found" error.

Reset everything (databases included): `docker compose down -v`

## gRPC (M5)

Order (50051), Knowledge (50052) and Ticket (50053) also serve gRPC, published on `127.0.0.1` only (development).
Every call needs a service token (HS256, `SERVICE_TOKEN_SECRET` in `.env`).

Host-side tools (code generation and the check script) need these exact versions; a virtual environment is easiest:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install grpcio-tools==1.85.1 grpcio==1.85.1 protobuf==7.35.1 PyJWT==2.15.1
python scripts/generate_proto.py      # only after changing a file in proto/ (the generated code is committed)
python scripts/grpc_client_check.py   # with the stack up and seeded: one PASS/FAIL line per step
```

`grpcio-tools` is installed on the host only, never in the services. It bundles protoc 35.1, whose generated code
requires `protobuf>=7.35.1` and `grpcio>=1.85.1` at runtime; the services pin exactly those, so change all three together.

## Events and Realtime (M6)

The Ticket service publishes events to RabbitMQ (`support.events`); the Realtime service pushes them to browsers
over WebSockets (`ws://localhost:8080/ws/connect`). Realtime can run as several instances:

```bash
docker compose up --build --scale realtime=2
```

Scale on `up`: Nginx resolves the `realtime` replicas when it starts. If you change the number of replicas while
Nginx is already running, run `docker compose restart nginx`.

Watch it live in the customer app and the agent dashboard (M11, M12), each tab signed in as a different user.

## AI agent (M7)

When a customer writes on a ticket, the agent answers within a few seconds using the policy documents.
`agent-consumer` turns `ticket.message_created` events into Celery tasks, `agent-worker` runs the LangGraph agent.

```bash
docker compose logs -f agent-worker agent-consumer
python scripts/check_agent_answers.py   # on the host (standard library only): 8 scenarios with PASS/FAIL
```

Staff can see every run (answered, skipped, failed) at `GET /api/agent/runs` (Swagger: `/api/agent/docs`).

## AI agent order tools (M8)

The agent can list a customer's orders, show one order with tracking, check whether an item can be returned, start
a return when the customer clearly asks, and hand the ticket to a human (`escalated`). The customer is always the
ticket's customer, injected by code; the model never chooses it.

The demo orders' dates are fixed when the order seed first runs, so return windows expire as days pass. Refresh them
(this deletes and recreates only the eleven demo orders, with their shipments, return requests and refunds; item ids change):

```bash
docker compose exec order python seed.py --reset
python scripts/check_agent_orders.py    # on the host (standard library only): 10 order scenarios with PASS/FAIL
```

## Approval and refund flow (M9)

The AI can **request** a refund for a returned item (it never executes one and never chooses the amount).
Refunds of ₹5,000 or less are approved by policy straight away; larger ones wait for a support agent, and the AI's
conversation pauses until then. Every approved refund is executed by `order-worker` only after it has verified the
approval with the Ticket service over gRPC (status, return request and exact amount).

```bash
docker compose logs -f order-worker order-consumer agent-worker   # refunds, verification failures, pause/resume
docker compose exec order python seed.py --reset                  # eleven demo orders (ORD-1011 is new)
python scripts/check_refund_flow.py                               # on the host (standard library only): 7 scenarios
```

Staff decide in the agent dashboard (M12, Approvals page), or with the Ticket API:
`POST /api/tickets/approvals/{id}/approve` or `.../reject` with `{"note": "..."}` (Swagger: `/api/tickets/docs`).

## Human takeover and AI activity log (M10)

A support agent can take over a conversation (`POST /api/tickets/tickets/{id}/takeover`), which silences the AI on
that ticket, and hand it back (`POST /api/tickets/tickets/{id}/release`). Staff can read exactly what the AI did on
every run (tool calls with arguments and results, retrieved knowledge-base chunks, reply, tokens, duration):
`GET /api/agent/tickets/{id}/activity` and `GET /api/agent/runs/{run_id}` (Swagger: `/api/agent/docs`).

```bash
python scripts/generate_proto.py                  # only after changing a .proto (ticket.proto changed in M10)
docker compose up --build                         # runs the two new migrations (ticket 0002, agent 0004)
python scripts/grpc_client_check.py               # 30 steps, incl. takeover: an ai PostMessage is ALREADY_EXISTS
docker compose exec order python seed.py --reset
python scripts/check_takeover_activity.py         # on the host (standard library only): 9 scenarios, ~2 minutes
```

## Agent dashboard (M12)

http://localhost:8080/agent/ for support staff (`agent1@example.com`, `agent2@example.com`, `admin@example.com`,
password `Password123!`): the ticket queue with filters, each ticket's conversation and AI activity log, take over /
release / reply / assign / change status, and the refund approvals with the return request for context. Everything
updates live over the WebSocket. Sign in to the customer app (http://localhost:8080/customer/) in another tab to play
the customer: each tab has its own session.

```bash
docker compose restart nginx                     # once, after the nginx.conf change (frontend files need no restart)
docker compose exec order python seed.py --reset # fresh demo orders before trying the refund flows
```

## URLs

| What | URL |
|---|---|
| Customer app | http://localhost:8080/customer/ |
| Agent dashboard | http://localhost:8080/agent/ |
| Identity API | http://localhost:8080/api/identity/health (Swagger: `/api/identity/docs`) |
| Ticket API | http://localhost:8080/api/tickets/health (Swagger: `/api/tickets/docs`) |
| Order API | http://localhost:8080/api/orders/health (Swagger: `/api/orders/docs`) |
| Knowledge API | http://localhost:8080/api/knowledge/health (Swagger: `/api/knowledge/docs`) |
| Agent API | http://localhost:8080/api/agent/health (Swagger: `/api/agent/docs`) |
| Realtime | http://localhost:8080/ws/health (WebSocket: `ws://localhost:8080/ws/connect`) |
| RabbitMQ management UI | http://localhost:15672 (user/password from `.env`) |
