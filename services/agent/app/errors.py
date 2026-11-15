# Plain exceptions raised by services. Routers map each one to an HTTP status code (ToolCallError: see below).


class NotFoundError(Exception):
    pass


class UnauthorizedError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class ConflictError(Exception):
    pass


class BadRequestError(Exception):
    pass


# Raised by the gRPC clients (app/agent/clients.py) for an expected business error from another service
# (NOT_FOUND, INVALID_ARGUMENT, ALREADY_EXISTS). The graph's ToolNode returns its message to the model as the tool
# result, so the model can explain it. Every other gRPC error is NOT converted and makes the run fail and retry.
class ToolCallError(Exception):
    pass
