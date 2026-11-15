# gRPC entry points for the ticket service. They sit at the same level as the REST routers:
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
from app.generated import ticket_pb2, ticket_pb2_grpc
from app.schemas import ApprovalCreate, MessageCreate
from app.services import approval_service, ticket_service

logger = logging.getLogger(__name__)

# Service names whose tokens this server accepts (GRPC_ALLOWED_CALLERS, e.g. "agent" or "agent,order")
ALLOWED_CALLERS = [name.strip() for name in settings.grpc_allowed_callers.split(",") if name.strip()]

# The REST API can only create customer and agent messages; gRPC callers can only create these two
GRPC_SENDER_TYPES = ("ai", "system")


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


# The caller (the AI agent) is trusted with any ticket id: the ticket id is the agent's conversation thread
class TicketServicer(ticket_pb2_grpc.TicketServiceServicer):
    def GetTicket(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input
            ticket_id = uuid.UUID(request.ticket_id)

            # Step 2: call the service (no customer filter, like the staff REST path)
            result = ticket_service.get_ticket(db, ticket_id)
            ticket = result["ticket"]

            # Step 3: convert the messages (oldest first) to proto
            message_items = []
            for message in result["messages"]:
                message_items.append(ticket_pb2.Message(
                    id=str(message.id),
                    sender_type=message.sender_type,
                    sender_id=str(message.sender_id) if message.sender_id else None,
                    content=message.content,
                    created_at=message.created_at.isoformat(),
                ))

            # Step 4: convert the ticket to proto
            return ticket_pb2.Ticket(
                id=str(ticket.id),
                customer_id=str(ticket.customer_id),
                customer_email=ticket.customer_email,
                subject=ticket.subject,
                status=ticket.status,
                assigned_agent_id=str(ticket.assigned_agent_id) if ticket.assigned_agent_id else None,
                created_at=ticket.created_at.isoformat(),
                updated_at=ticket.updated_at.isoformat(),
                messages=message_items,
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def PostMessage(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input (the content with the same schema the REST route uses)
            ticket_id = uuid.UUID(request.ticket_id)
            if request.sender_type not in GRPC_SENDER_TYPES:
                raise BadRequestError(f"sender_type must be one of: {', '.join(GRPC_SENDER_TYPES)}")
            body = MessageCreate(content=request.content)

            # Step 2: call the service (ai and system messages have no sender_id)
            message = ticket_service.add_message(db, ticket_id, request.sender_type, None, body.content)

            # Step 3: convert to proto
            return ticket_pb2.Message(
                id=str(message.id),
                sender_type=message.sender_type,
                content=message.content,
                created_at=message.created_at.isoformat(),
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def CreateApproval(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input with the same schema the REST route uses
            ticket_id = uuid.UUID(request.ticket_id)
            body = ApprovalCreate(
                action_type=request.action_type,
                return_request_id=request.return_request_id,
                amount=request.amount,
                summary=request.summary,
            )

            # Step 2: call the service (auto-approve limit, one approval per return request, ticket status)
            approval = approval_service.create_approval(
                db, ticket_id, body.action_type, body.return_request_id, body.amount, body.summary
            )

            # Step 3: convert to proto
            return ticket_pb2.Approval(
                id=str(approval.id),
                ticket_id=str(approval.ticket_id),
                action_type=approval.action_type,
                return_request_id=str(approval.return_request_id),
                amount=f"{approval.amount:.2f}",
                summary=approval.summary,
                status=approval.status,
                auto_approved=approval.auto_approved,
                created_at=approval.created_at.isoformat(),
            )
        except Exception as error:
            abort_from_error(context, error)
        finally:
            db.close()

    def EscalateTicket(self, request, context):
        db = SessionLocal()
        try:
            # Step 1: validate the input
            ticket_id = uuid.UUID(request.ticket_id)

            # Step 2: call the same service function as the staff REST route. It publishes ticket.status_changed
            # only when the status really changes, so escalating an escalated ticket again changes nothing.
            # The reason is only logged here, not stored (the agent service records it on its run).
            ticket = ticket_service.update_status(db, ticket_id, "escalated")
            logger.info("Ticket %s escalated over gRPC: %s", ticket.id, request.reason)

            # Step 3: convert to proto (without the messages; GetTicket returns those)
            return ticket_pb2.Ticket(
                id=str(ticket.id),
                customer_id=str(ticket.customer_id),
                customer_email=ticket.customer_email,
                subject=ticket.subject,
                status=ticket.status,
                assigned_agent_id=str(ticket.assigned_agent_id) if ticket.assigned_agent_id else None,
                created_at=ticket.created_at.isoformat(),
                updated_at=ticket.updated_at.isoformat(),
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
    ticket_pb2_grpc.add_TicketServiceServicer_to_server(TicketServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")

    # Step 3: start serving in the background
    server.start()
    logger.info("gRPC server listening on port %s (allowed callers: %s)", settings.grpc_port, ", ".join(ALLOWED_CALLERS))
    return server


# Called from main.py at shutdown: finish running calls (up to 5 seconds), then stop
def stop_grpc_server(server: grpc.Server) -> None:
    server.stop(grace=5).wait()
    logger.info("gRPC server stopped")
