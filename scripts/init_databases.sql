-- Runs once, when the Postgres volume is first created (mounted into /docker-entrypoint-initdb.d).
-- To run it again: docker compose down -v && docker compose up --build

-- Step 1: one database per service (realtime has no database)
CREATE DATABASE identity_db;
CREATE DATABASE ticket_db;
CREATE DATABASE order_db;
CREATE DATABASE knowledge_db;
CREATE DATABASE agent_db;

-- Step 2: enable pgvector in the knowledge database
\connect knowledge_db
CREATE EXTENSION IF NOT EXISTS vector;
