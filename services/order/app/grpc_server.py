# gRPC entry points for the order service. They sit at the same level as the REST routers:
# each method opens a session, validates the input, calls the same service function the REST route calls,
# and converts the result to a proto message. No business rules live here.
import logging
import uuid
from concurrent import futures

import grpc
import jwt

from app.config import settings
from app.database import SessionLocal
from app.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.generated import order_pb2, order_pb2_grpc
from app.schemas import ReturnRequestCreate
from app.services import order_service, return_service

logger = logging.getLogger(__name__)

# Service names whose tokens this server accepts (GRPC_ALLOWED_CALLERS, e.g. "agent" or "agent,order")
ALLOWED_CALLERS = [name.strip() for name in settings.grpc_allowed_callers.split(",") if name.strip()]


# The one place where service errors become gRPC status codes. context.abort raises, so this never returns.
# ValueError covers Pydantic's ValidationError and a malformed UUID.
def abort_from_error(context: grpc.ServicerContext, error: Exception) -> None:
    if isinstance(error, NotFoundError):
        context.abort(grpc.StatusCode.NOT_FOUND, str(error))
    if isinstance(error, UnauthorizedError):
        context.abort(grpc.StatusCode.UNAUTHENTICATED, str(error))
    if isinstance(error, ForbiddenError):
        context.abort(grpc.StatusCode.PERMISSION_DENIED, str(error))
    if isinstance(error, ConflictError):
        context.abort(grpc.StatusCode.ALREADY_EXISTS, str(error))
    if isinstance(error, (BadRequestError, ValueError)):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
    logger.exception("Unexpected error in a gRPC call")
    context.abort(grpc.StatusCode.INTERNAL, "Internal server error")


# The caller (the AI agent) always supplies customer_id, so every lookup is limited to that customer's orders
class OrderServicer(order_pb2_grpc.OrderServiceServicer):
    def ListOrders(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input
            customer_id = uuid.UUID(request.customer_id)

            # Step 2: call the service
            orders = order_service.list_orders(db, customer_id)

            # Step 3: convert each order (with its items and shipment) to proto
            order_messages = []
            for order in orders:
                items = []
                for item in order.items:
                    items.append(order_pb2.OrderItem(
                        id=str(item.id),
                        product_name=item.product_name,
                        category=item.category,
                        quantity=item.quantity,
                        unit_price=f"{item.unit_price:.2f}",
                    ))
                shipment = None
                if order.shipment is not None:
                    shipment = order_pb2.Shipment(
                        carrier=order.shipment.carrier,
                        tracking_number=order.shipment.tracking_number,
                        status=order.shipment.status,
                        shipped_on=order.shipment.shipped_on.isoformat(),
                        estimated_delivery=order.shipment.estimated_delivery.isoformat(),
                        delivered_on=order.shipment.delivered_on.isoformat() if order.shipment.delivered_on else None,
                    )
                order_messages.append(order_pb2.Order(
                    order_number=order.order_number,
                    customer_id=str(order.customer_id),
                    status=order.status,
                    total_amount=f"{order.total_amount:.2f}",
                    ordered_at=order.ordered_at.isoformat(),
                    items=items,
                    shipment=shipment,
                ))
            return order_pb2.ListOrdersResponse(orders=order_messages)
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def GetOrder(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input
            customer_id = uuid.UUID(request.customer_id)

            # Step 2: call the service (another customer's order is NOT_FOUND)
            order = order_service.get_order(db, request.order_number, customer_id)

            # Step 3: convert the items and the shipment (absent for placed and cancelled orders) to proto
            items = []
            for item in order.items:
                items.append(order_pb2.OrderItem(
                    id=str(item.id),
                    product_name=item.product_name,
                    category=item.category,
                    quantity=item.quantity,
                    unit_price=f"{item.unit_price:.2f}",
                ))
            shipment = None
            if order.shipment is not None:
                shipment = order_pb2.Shipment(
                    carrier=order.shipment.carrier,
                    tracking_number=order.shipment.tracking_number,
                    status=order.shipment.status,
                    shipped_on=order.shipment.shipped_on.isoformat(),
                    estimated_delivery=order.shipment.estimated_delivery.isoformat(),
                    delivered_on=order.shipment.delivered_on.isoformat() if order.shipment.delivered_on else None,
                )

            # Step 4: convert the order itself
            return order_pb2.Order(
                order_number=order.order_number,
                customer_id=str(order.customer_id),
                status=order.status,
                total_amount=f"{order.total_amount:.2f}",
                ordered_at=order.ordered_at.isoformat(),
                items=items,
                shipment=shipment,
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def CheckReturnEligibility(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input
            customer_id = uuid.UUID(request.customer_id)
            order_item_id = uuid.UUID(request.order_item_id)

            # Step 2: call the service (an ineligible item is a normal answer, not an error)
            result = return_service.check_return_eligibility(db, request.order_number, order_item_id, customer_id)

            # Step 3: convert to proto (window_days is 0 when no window applies)
            return order_pb2.ReturnEligibility(
                eligible=result["eligible"],
                reason_code=result["reason_code"],
                reason=result["reason"],
                refund_amount=f"{result['refund_amount']:.2f}" if result["refund_amount"] is not None else None,
                window_days=result["window_days"] or 0,
                days_remaining=result["days_remaining"],
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def CreateReturnRequest(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input with the same schema the REST route uses
            customer_id = uuid.UUID(request.customer_id)
            body = ReturnRequestCreate(
                order_number=request.order_number,
                order_item_id=request.order_item_id,
                reason=request.reason,
            )

            # Step 2: call the service (not eligible is INVALID_ARGUMENT, already requested is ALREADY_EXISTS)
            return_request = return_service.create_return_request(
                db, customer_id, body.order_number, body.order_item_id, body.reason
            )

            # Step 3: convert to proto
            return order_pb2.ReturnRequest(
                id=str(return_request.id),
                order_number=body.order_number,
                order_item_id=str(return_request.order_item_id),
                status=return_request.status,
                refund_amount=f"{return_request.refund_amount:.2f}",
                reason=return_request.reason,
                created_at=return_request.created_at.isoformat(),
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()


# Checks the service token on every call before any servicer code runs.
# The caller sends "authorization: Bearer <token>", an HS256 JWT signed with SERVICE_TOKEN_SECRET with
# sub = the calling service's name, iat, and exp (60 seconds later). This is service identity only;
# the end user is still the customer_id the trusted caller puts in the request.
class ServiceTokenInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        # Step 1: find the bearer token in the call metadata
        token = None
        for key, value in handler_call_details.invocation_metadata:
            if key == "authorization" and value.startswith("Bearer "):
                token = value.removeprefix("Bearer ")

        # Step 2: check the signature and expiry, then the caller name against the allowlist
        rejection = None
        if token is None:
            rejection = (grpc.StatusCode.UNAUTHENTICATED, "Missing service token")
        else:
            try:
                claims = jwt.decode(
                    token,
                    settings.service_token_secret,
                    algorithms=["HS256"],
                    options={"require": ["sub", "iat", "exp"]},
                )
                if claims["sub"] not in ALLOWED_CALLERS:
                    rejection = (grpc.StatusCode.PERMISSION_DENIED, f"Caller '{claims['sub']}' is not allowed")
            except jwt.InvalidTokenError:
                rejection = (grpc.StatusCode.UNAUTHENTICATED, "Invalid or expired service token")

        # Step 3: a valid token from an allowed caller goes on to the servicer
        if rejection is None:
            return continuation(handler_call_details)

        # Step 4: otherwise answer with a handler that aborts straight away (all our RPCs are unary-unary)
        code, details = rejection
        logger.warning("Rejected gRPC call to %s: %s", handler_call_details.method, details)
        return grpc.unary_unary_rpc_method_handler(lambda request, context: context.abort(code, details))


# Called from main.py at startup. server.start() does not block: gRPC serves calls on its own thread pool.
def start_grpc_server() -> grpc.Server:
    # Step 1: create a synchronous server with a thread pool; every call passes the service token check first
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=[ServiceTokenInterceptor()])

    # Step 2: register the servicer and listen on the gRPC port
    order_pb2_grpc.add_OrderServiceServicer_to_server(OrderServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")

    # Step 3: start serving in the background
    server.start()
    logger.info("gRPC server listening on port %s (allowed callers: %s)", settings.grpc_port, ", ".join(ALLOWED_CALLERS))
    return server


# Called from main.py at shutdown: finish running calls (up to 5 seconds), then stop
def stop_grpc_server(server: grpc.Server) -> None:
    server.stop(grace=5).wait()
    logger.info("gRPC server stopped")
