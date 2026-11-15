import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # The customer lives in the identity service's database, so there is no foreign key
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20))  # placed | shipped | delivered | cancelled
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.product_name")
    shipment: Mapped["Shipment | None"] = relationship()


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    # M9: the item's return request, if one exists (at most one: order_item_id is unique there). Read-only here;
    # return requests are created through return_repository.
    return_request: Mapped["ReturnRequest | None"] = relationship(viewonly=True)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # unique: one shipment per order
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    carrier: Mapped[str] = mapped_column(String(100))
    tracking_number: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # in_transit | delivered
    shipped_on: Mapped[date] = mapped_column(Date)
    estimated_delivery: Mapped[date] = mapped_column(Date)
    delivered_on: Mapped[date | None] = mapped_column(Date)


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    # unique: an order item can be returned only once
    order_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_items.id"), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(500))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20))  # requested | refunded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # both unique: these two constraints are what makes refund execution idempotent
    return_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("return_requests.id"), unique=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20))  # completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
