from __future__ import annotations

AUTH_UNAUTHORIZED = "AUTH_UNAUTHORIZED"
AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
AUTH_READ_ONLY = "AUTH_READ_ONLY"


def auth_error(code: str, detail: str, status: int = 401) -> dict:
    return {"detail": detail, "code": code, "status": status}


def unauthorized(detail: str = "需要访问令牌") -> dict:
    return auth_error(AUTH_UNAUTHORIZED, detail, 401)


def forbidden(detail: str = "权限不足") -> dict:
    return auth_error(AUTH_FORBIDDEN, detail, 403)


def read_only(detail: str = "只读令牌不能生成教程") -> dict:
    return auth_error(AUTH_READ_ONLY, detail, 403)
