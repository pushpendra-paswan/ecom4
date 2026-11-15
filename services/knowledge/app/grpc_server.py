# gRPC entry points for the knowledge service. They sit at the same level as the REST routers:
# each method validates the input, calls the same service function the REST route calls,
# and converts the result to a proto message. No business rules live here.
import logging
from concurrent import futures

import grpc
import jwt

from app.config import settings
from app.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.generated import knowledge_pb2, knowledge_pb2_grpc
from app.schemas import SearchRequest
from app.services import search_service

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


class KnowledgeServicer(knowledge_pb2_grpc.KnowledgeServiceServicer):
    # No database session here: search_service goes through rag.py, which uses the engine directly
    def Search(self, request, context):
        try:
            # Step 1: validate the input with the same schema the REST route uses (top_k 0 means "not set")
            top_k = request.top_k if request.top_k != 0 else search_service.DEFAULT_TOP_K
            body = SearchRequest(query=request.query, top_k=top_k)

            # Step 2: call the service (this makes an embedding call to OpenAI)
            results = search_service.search_knowledge(body.query, body.top_k)

            # Step 3: convert to proto, best match first
            result_messages = []
            for result in results:
                result_messages.append(knowledge_pb2.SearchResult(
                    document_id=str(result["document_id"]),
                    title=result["title"],
                    section=result["section"],
                    content=result["content"],
                    score=result["score"],
                ))
            return knowledge_pb2.SearchResponse(results=result_messages)
        except Exception as error:
            abort_from_error(context, error)


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
    knowledge_pb2_grpc.add_KnowledgeServiceServicer_to_server(KnowledgeServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")

    # Step 3: start serving in the background
    server.start()
    logger.info("gRPC server listening on port %s (allowed callers: %s)", settings.grpc_port, ", ".join(ALLOWED_CALLERS))
    return server


# Called from main.py at shutdown: finish running calls (up to 5 seconds), then stop
def stop_grpc_server(server: grpc.Server) -> None:
    server.stop(grace=5).wait()
    logger.info("gRPC server stopped")
