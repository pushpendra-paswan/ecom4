# Demo users with fixed ids, so later milestones can reference them in their own seed data.
# Run with: docker compose exec identity python seed.py   (safe to run again)
import uuid

from argon2 import PasswordHasher

from app.database import SessionLocal
from app.repositories import user_repository

DEMO_PASSWORD = "Password123!"

# (id, email, full name, role)
DEMO_USERS = [
    ("00000000-0000-0000-0000-000000000001", "admin@example.com", "Admin User", "admin"),
    ("00000000-0000-0000-0000-000000000011", "agent1@example.com", "Agent One", "agent"),
    ("00000000-0000-0000-0000-000000000012", "agent2@example.com", "Agent Two", "agent"),
    ("00000000-0000-0000-0000-000000000101", "customer1@example.com", "Customer One", "customer"),
    ("00000000-0000-0000-0000-000000000102", "customer2@example.com", "Customer Two", "customer"),
    ("00000000-0000-0000-0000-000000000103", "customer3@example.com", "Customer Three", "customer"),
]

password_hasher = PasswordHasher()
created_count = 0
skipped_count = 0

# Step 1: open a database session
db = SessionLocal()

# Step 2: create each demo user unless the email already exists
for user_id, email, full_name, role in DEMO_USERS:
    if user_repository.get_by_email(db, email) is not None:
        print(f"skipped  {email} (already exists)")
        skipped_count += 1
        continue
    password_hash = password_hasher.hash(DEMO_PASSWORD)
    user_repository.create(db, email, full_name, password_hash, role, uuid.UUID(user_id))
    print(f"created  {email} ({role})")
    created_count += 1

# Step 3: close the session and print a summary
db.close()
print(f"Done: {created_count} created, {skipped_count} skipped. Demo password: {DEMO_PASSWORD}")
