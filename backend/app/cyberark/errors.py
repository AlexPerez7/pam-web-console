class CyberArkError(Exception):
    """Base de todos los errores del cliente CyberArk."""


class AuthError(CyberArkError):
    pass


class NotFound(CyberArkError):
    pass


class Forbidden(CyberArkError):
    pass


class Conflict(CyberArkError):
    pass


class RateLimited(CyberArkError):
    pass


class UpstreamError(CyberArkError):
    """5xx o respuesta inesperada del PVWA."""
