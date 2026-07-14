"""
ToonFlow Backend — SafeError / Exception Hierarchy
"""
from __future__ import annotations


class SafeError(Exception):
    """User-safe error with stable code and correlation ID.

    Never exposes internal stack traces, secrets, or provider internals.
    """

    def __init__(
        self,
        code: str,
        message: str = "安全错误",
        status_code: int = 400,
        correlation_id: str | None = None,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.correlation_id = correlation_id
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "correlation_id": self.correlation_id,
            }
        }


class NotFoundError(SafeError):
    def __init__(self, entity: str, id: str, correlation_id: str | None = None):
        super().__init__(
            code="NOT_FOUND",
            message=f"{entity} 未找到",
            status_code=404,
            correlation_id=correlation_id,
        )


class UnauthorizedError(SafeError):
    def __init__(self, correlation_id: str | None = None):
        super().__init__(
            code="UNAUTHORIZED",
            message="未授权访问",
            status_code=401,
            correlation_id=correlation_id,
        )


class ForbiddenError(SafeError):
    def __init__(self, reason: str = "权限不足", correlation_id: str | None = None):
        super().__init__(
            code="FORBIDDEN",
            message=reason,
            status_code=403,
            correlation_id=correlation_id,
        )


class ConflictError(SafeError):
    def __init__(self, message: str = "资源冲突", correlation_id: str | None = None, details: dict | None = None):
        super().__init__(
            code="CONFLICT",
            message=message,
            status_code=409,
            correlation_id=correlation_id,
            details=details,
        )


class ValidationError_(SafeError):
    def __init__(self, message: str = "校验失败", details: dict | None = None, correlation_id: str | None = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=422,
            correlation_id=correlation_id,
            details=details,
        )


class CrossOwnerError(ForbiddenError):
    def __init__(self, correlation_id: str | None = None):
        super().__init__(
            reason="跨 owner 访问被拒绝",
            correlation_id=correlation_id,
        )


class PolicyBlockedError(SafeError):
    def __init__(self, reason: str, correlation_id: str | None = None):
        super().__init__(
            code="POLICY_BLOCKED",
            message=reason,
            status_code=403,
            correlation_id=correlation_id,
        )
