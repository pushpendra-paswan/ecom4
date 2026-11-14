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
| Realtime | http://localhost:8080/ws/health (WebSockets under `ws://localhost:8080/ws/`) |
| RabbitMQ management UI | http://localhost:15672 (user/password from `.env`) |
