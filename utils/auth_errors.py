from __future__ import annotations

AUTH_UNAUTHORIZED = "AUTH_UNAUTHORIZED"
AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
AUTH_READ_ONLY = "AUTH_READ_ONLY"
AUTH_DEMO = "AUTH_DEMO"
AUTH_LOGIN_INVALID = "AUTH_LOGIN_INVALID"

DETAIL_UNAUTHORIZED = "需要访问令牌（AUTH_UNAUTHORIZED / 401）"
DETAIL_FORBIDDEN = "权限不足（AUTH_FORBIDDEN / 403）"
DETAIL_READ_ONLY = "只读令牌不能生成、删除或备份（AUTH_READ_ONLY / 403）"
DETAIL_DEMO = "演示模式禁止生成（AUTH_DEMO / 403）"
DETAIL_LOGIN_INVALID = "令牌不正确（AUTH_LOGIN_INVALID / 401）。只读令牌请用 Bearer，不要走此表单。"


def auth_error(code: str, detail: str, status: int = 401) -> dict:
    return {"detail": detail, "code": code, "status": status}


def unauthorized(detail: str = DETAIL_UNAUTHORIZED) -> dict:
    return auth_error(AUTH_UNAUTHORIZED, detail, 401)


def forbidden(detail: str = DETAIL_FORBIDDEN) -> dict:
    return auth_error(AUTH_FORBIDDEN, detail, 403)


def read_only(detail: str = DETAIL_READ_ONLY) -> dict:
    return auth_error(AUTH_READ_ONLY, detail, 403)


def demo_forbidden(detail: str = DETAIL_DEMO) -> dict:
    return auth_error(AUTH_DEMO, detail, 403)


def login_invalid(detail: str = DETAIL_LOGIN_INVALID) -> dict:
    return auth_error(AUTH_LOGIN_INVALID, detail, 401)


def auth_code_catalog() -> list[dict]:
    return [
        unauthorized(),
        forbidden(),
        read_only(),
        demo_forbidden(),
        login_invalid(),
    ]
