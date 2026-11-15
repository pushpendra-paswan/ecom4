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

Throwaway test page (open it in several tabs, one per user): http://localhost:8080/test/realtime.html

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
| Realtime test page (M6, throwaway) | http://localhost:8080/test/realtime.html |
| RabbitMQ management UI | http://localhost:15672 (user/password from `.env`) |
