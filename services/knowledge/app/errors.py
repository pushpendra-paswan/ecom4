# Plain exceptions raised by services. Routers map each one to an HTTP status code.


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
