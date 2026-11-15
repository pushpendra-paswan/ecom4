# gRPC client for the Ticket service (M9): the Order service reads an approval to verify it before refunding.
import time

import grpc
import jwt

from app.config import settings
from app.generated import ticket_pb2, ticket_pb2_grpc

# Deadline (seconds), the M5 convention
TIMEOUT_SECONDS = 5
SERVICE_TOKEN_LIFETIME_SECONDS = 60

# One channel per process, created on first use and then reused (channels are thread-safe).
# Not created at import time: Celery imports this module BEFORE it forks its worker processes,
# and a gRPC channel must not be shared across a fork.
ticket_stub = None


# Returns the approval as a plain dict, or None if the Ticket service does not know it (NOT_FOUND).
# Every other gRPC error (UNAVAILABLE, DEADLINE_EXCEEDED, ...) is raised unchanged; the Celery task decides
# whether to retry.
def get_approval(approval_id: str) -> dict | None:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: a fresh service token for this call: HS256, signed with the shared secret, sub=order, 60 seconds
    issued_at = int(time.time())
    claims = {"sub": "order", "iat": issued_at, "exp": issued_at + SERVICE_TOKEN_LIFETIME_SECONDS}
    token = jwt.encode(claims, settings.service_token_secret, algorithm="HS256")

    # Step 3: call the Ticket service
    try:
        approval = ticket_stub.GetApproval(
            ticket_pb2.GetApprovalRequest(approval_id=approval_id),
            timeout=TIMEOUT_SECONDS,
            metadata=[("authorization", f"Bearer {token}")],
        )
    except grpc.RpcError as error:
        if error.code() == grpc.StatusCode.NOT_FOUND:
            return None
        raise

    # Step 4: convert to plain Python (None where a decision field is absent)
    return {
        "id": approval.id,
        "ticket_id": approval.ticket_id,
        "action_type": approval.action_type,
        "return_request_id": approval.return_request_id,
        "amount": approval.amount,
        "summary": approval.summary,
        "status": approval.status,
        "auto_approved": approval.auto_approved,
        "created_at": approval.created_at,
        "decision_note": approval.decision_note if approval.HasField("decision_note") else None,
        "decided_by": approval.decided_by if approval.HasField("decided_by") else None,
        "decided_at": approval.decided_at if approval.HasField("decided_at") else None,
    }
