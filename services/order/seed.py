# Demo orders for the fixed demo customers created by the identity seed.
# Run with: docker compose exec order python seed.py           (safe to run again; existing orders are skipped)
#      or:  docker compose exec order python seed.py --reset   (deletes the ten demo orders with their shipments,
#           return requests and refunds, then creates them again with dates relative to today)
# Dates are relative to the day the seed runs, so return windows expire as the days pass: use --reset to refresh them.
# --reset gives the items new ids. Only the ten demo order numbers are touched.
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import Order, OrderItem, Refund, ReturnRequest, Shipment
from app.repositories import order_repository

CUSTOMER_1 = uuid.UUID("00000000-0000-0000-0000-000000000101")
CUSTOMER_2 = uuid.UUID("00000000-0000-0000-0000-000000000102")
CUSTOMER_3 = uuid.UUID("00000000-0000-0000-0000-000000000103")

# Each order: number, customer, status, how many days ago it was delivered (delivered orders only),
# items as (product name, category, unit price), and the scenario it is for.
# Quantity is always 1.
DEMO_ORDERS = [
    {
        "order_number": "ORD-1001", "customer_id": CUSTOMER_1, "status": "delivered", "delivered_days_ago": 5,
        "items": [("Wireless headphones", "electronics", "4500"), ("Phone case", "accessories", "800")],
    },
    {
        "order_number": "ORD-1002", "customer_id": CUSTOMER_1, "status": "delivered", "delivered_days_ago": 40,
        "items": [("T-shirt", "clothing", "1200")],
    },
    {
        "order_number": "ORD-1003", "customer_id": CUSTOMER_1, "status": "shipped", "delivered_days_ago": None,
        "items": [("Backpack", "accessories", "2200")],
    },
    {
        "order_number": "ORD-1004", "customer_id": CUSTOMER_2, "status": "delivered", "delivered_days_ago": 10,
        "items": [("Smartwatch", "electronics", "18000")],
    },
    {
        "order_number": "ORD-1005", "customer_id": CUSTOMER_2, "status": "delivered", "delivered_days_ago": 3,
        "items": [("Gift card", "gift_card", "2000")],
    },
    {
        "order_number": "ORD-1006", "customer_id": CUSTOMER_2, "status": "delivered", "delivered_days_ago": 20,
        "items": [("Bluetooth speaker", "electronics", "3500")],
    },
    {
        "order_number": "ORD-1007", "customer_id": CUSTOMER_2, "status": "delivered", "delivered_days_ago": 7,
        "items": [("Jacket", "clothing", "2500")],
        "return_reason": "The jacket is too small",  # this order gets a return request with status "requested"
    },
    {
        "order_number": "ORD-1008", "customer_id": CUSTOMER_3, "status": "delivered", "delivered_days_ago": 12,
        "items": [("Running shoes", "clothing", "3000"), ("Socks", "clothing", "400")],
    },
    {
        "order_number": "ORD-1009", "customer_id": CUSTOMER_3, "status": "placed", "delivered_days_ago": None,
        "items": [("Desk lamp", "electronics", "1500")],
    },
    {
        "order_number": "ORD-1010", "customer_id": CUSTOMER_3, "status": "cancelled", "delivered_days_ago": None,
        "items": [("Notebook set", "accessories", "600")],
    },
]

created_count = 0
skipped_count = 0
today = date.today()

# Step 1: open a database session
db = SessionLocal()

# Step 2 (only with --reset): delete the demo orders and everything that points at them, children first,
# in one transaction
if "--reset" in sys.argv[1:]:
    demo_order_numbers = [demo_order["order_number"] for demo_order in DEMO_ORDERS]
    order_ids = list(db.scalars(select(Order.id).where(Order.order_number.in_(demo_order_numbers))))
    return_request_ids = list(db.scalars(select(ReturnRequest.id).where(ReturnRequest.order_id.in_(order_ids))))
    refund_result = db.execute(delete(Refund).where(Refund.return_request_id.in_(return_request_ids)))
    return_result = db.execute(delete(ReturnRequest).where(ReturnRequest.id.in_(return_request_ids)))
    db.execute(delete(Shipment).where(Shipment.order_id.in_(order_ids)))
    db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
    db.execute(delete(Order).where(Order.id.in_(order_ids)))
    db.commit()
    print(
        f"reset    deleted {len(order_ids)} demo orders, {return_result.rowcount} return requests "
        f"and {refund_result.rowcount} refunds"
    )

# Step 3: create each demo order unless the order number already exists
for demo_order in DEMO_ORDERS:
    order_number = demo_order["order_number"]
    if order_repository.get_by_order_number(db, order_number) is not None:
        print(f"skipped  {order_number} (already exists)")
        skipped_count += 1
        continue

    # Step 4: work out the dates from the status
    if demo_order["status"] == "delivered":
        delivered_on = today - timedelta(days=demo_order["delivered_days_ago"])
        shipped_on = delivered_on - timedelta(days=3)
        ordered_on = delivered_on - timedelta(days=5)
    elif demo_order["status"] == "shipped":
        shipped_on = today - timedelta(days=2)
        ordered_on = today - timedelta(days=4)
    elif demo_order["status"] == "placed":
        ordered_on = today - timedelta(days=1)
    else:
        ordered_on = today - timedelta(days=6)

    # Step 5: create the order with its items
    total_amount = sum(Decimal(unit_price) for _, _, unit_price in demo_order["items"])
    order = Order(
        order_number=order_number,
        customer_id=demo_order["customer_id"],
        status=demo_order["status"],
        total_amount=total_amount,
        ordered_at=datetime.combine(ordered_on, time(10, 0), tzinfo=timezone.utc),
        items=[
            OrderItem(product_name=product_name, category=category, quantity=1, unit_price=Decimal(unit_price))
            for product_name, category, unit_price in demo_order["items"]
        ],
    )

    # Step 6: delivered and shipped orders have one shipment (placed and cancelled orders have none)
    if demo_order["status"] == "delivered":
        order.shipment = Shipment(
            carrier="BlueDart", tracking_number=f"BD-{order_number[4:]}", status="delivered",
            shipped_on=shipped_on, estimated_delivery=delivered_on, delivered_on=delivered_on,
        )
    elif demo_order["status"] == "shipped":
        order.shipment = Shipment(
            carrier="Delhivery", tracking_number=f"DL-{order_number[4:]}", status="in_transit",
            shipped_on=shipped_on, estimated_delivery=today + timedelta(days=3), delivered_on=None,
        )

    # Step 7: save the order (flush gives the order and its items their ids)
    db.add(order)
    db.flush()

    # Step 8: ORD-1007 starts with a return request already open for its only item
    if "return_reason" in demo_order:
        item = order.items[0]
        db.add(ReturnRequest(
            order_id=order.id, order_item_id=item.id, customer_id=order.customer_id,
            reason=demo_order["return_reason"], refund_amount=item.unit_price, status="requested",
        ))

    db.commit()
    print(f"created  {order_number} ({demo_order['status']})")
    created_count += 1

# Step 9: close the session and print a summary
db.close()
print(f"Done: {created_count} created, {skipped_count} skipped.")
