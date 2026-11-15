# Demo tickets for the fixed demo users created by the identity seed.
# Run with: docker compose exec ticket python seed.py   (safe to run again: tickets have fixed ids and existing ones are skipped)
# Times are relative to the moment the seed runs.
import uuid
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import Message, Ticket
from app.repositories import ticket_repository

AGENT_1 = uuid.UUID("00000000-0000-0000-0000-000000000011")
CUSTOMER_1 = uuid.UUID("00000000-0000-0000-0000-000000000101")
CUSTOMER_2 = uuid.UUID("00000000-0000-0000-0000-000000000102")
CUSTOMER_3 = uuid.UUID("00000000-0000-0000-0000-000000000103")

# Each ticket: fixed id, customer, subject, status, assigned agent, how many hours ago it was opened,
# and its messages as (sender_type, sender_id, content, minutes after the ticket was opened)
DEMO_TICKETS = [
    {
        "id": "00000000-0000-0000-0000-000000000201",
        "customer_id": CUSTOMER_1, "customer_email": "customer1@example.com",
        "subject": "Question about my headphones", "status": "open", "assigned_agent_id": AGENT_1,
        "opened_hours_ago": 26,
        "messages": [
            ("customer", CUSTOMER_1, "The wireless headphones from order ORD-1001 crackle in the left ear at high volume. Is this covered?", 0),
            ("agent", AGENT_1, "Sorry about that! Please try a reset (hold the power button for 10 seconds). If it still crackles, you can return them within the 15-day electronics window.", 45),
        ],
    },
    {
        "id": "00000000-0000-0000-0000-000000000202",
        "customer_id": CUSTOMER_2, "customer_email": "customer2@example.com",
        "subject": "Smartwatch not working", "status": "open", "assigned_agent_id": None,
        "opened_hours_ago": 5,
        "messages": [
            ("customer", CUSTOMER_2, "My smartwatch from order ORD-1004 does not turn on, even after charging it overnight.", 0),
        ],
    },
    {
        "id": "00000000-0000-0000-0000-000000000203",
        "customer_id": CUSTOMER_3, "customer_email": "customer3@example.com",
        "subject": "Where is my order?", "status": "resolved", "assigned_agent_id": None,
        "opened_hours_ago": 50,
        "messages": [
            ("customer", CUSTOMER_3, "I placed order ORD-1009 (desk lamp) yesterday. When will it ship?", 0),
            ("agent", AGENT_1, "Your order is being packed and should ship within 2 days. You will get a tracking number as soon as it leaves the warehouse.", 30),
        ],
    },
]

created_count = 0
skipped_count = 0
now = datetime.now(timezone.utc)

# Step 1: open a database session
db = SessionLocal()

# Step 2: create each demo ticket unless its fixed id already exists
for demo_ticket in DEMO_TICKETS:
    ticket_id = uuid.UUID(demo_ticket["id"])
    if ticket_repository.get_by_id(db, ticket_id) is not None:
        print(f"skipped  {ticket_id} (already exists)")
        skipped_count += 1
        continue

    # Step 3: work out the times (updated_at is the time of the last message)
    opened_at = now - timedelta(hours=demo_ticket["opened_hours_ago"])
    last_message_minutes = demo_ticket["messages"][-1][3]
    updated_at = opened_at + timedelta(minutes=last_message_minutes)

    # Step 4: create the ticket (flush so its row exists before the messages)
    db.add(Ticket(
        id=ticket_id, customer_id=demo_ticket["customer_id"], customer_email=demo_ticket["customer_email"],
        subject=demo_ticket["subject"], status=demo_ticket["status"],
        assigned_agent_id=demo_ticket["assigned_agent_id"], created_at=opened_at, updated_at=updated_at,
    ))
    db.flush()

    # Step 5: add its messages with their own times
    for sender_type, sender_id, content, minutes_after_open in demo_ticket["messages"]:
        db.add(Message(
            ticket_id=ticket_id, sender_type=sender_type, sender_id=sender_id, content=content,
            created_at=opened_at + timedelta(minutes=minutes_after_open),
        ))

    db.commit()
    print(f"created  {ticket_id} \"{demo_ticket['subject']}\" ({demo_ticket['status']}, {len(demo_ticket['messages'])} message(s))")
    created_count += 1

# Step 6: close the session and print a summary
db.close()
print(f"Done: {created_count} created, {skipped_count} skipped.")
