from enum import Enum


class ErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"      # 429, FB code 1675004
    SESSION = "session"            # FB codes 1357004 / 1357001, 403 + rd_challenge
    TRANSIENT = "transient"        # FB code 368, 5xx
    FATAL = "fatal"                # anything else


class ExtractError(Exception):
    """Raised when extraction fails after retries; carries the classified kind."""

    def __init__(self, message: str, kind: ErrorKind = ErrorKind.FATAL):
        super().__init__(message)
        self.kind = kind
