from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Final, Self
from urllib.parse import quote

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_DIR: Final = Path(__file__).resolve().parent
_REPO_MARKERS: Final = (".git", "compose.yaml")


def find_repo_root(
    start: Path | None = None,
    markers: Sequence[str] = _REPO_MARKERS,
) -> Path:
    """Return the checkout root that owns ``start``, ignoring the process working directory.

    Container images copy only ``apps/api`` contents, so no marker exists there and the
    directory holding the ``app`` package is used instead; it matches the compose bind target.
    """
    origin = (_PACKAGE_DIR.parent if start is None else start).resolve()
    for candidate in (origin, *origin.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return origin


REPO_ROOT: Final = find_repo_root()


class Settings(BaseSettings):
    app_name: str = "Quick Study API"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "quickstudy"
    mysql_user: str = "quickstudy"
    mysql_password: str = ""
    database_url: str | None = None

    redis_url: str = "redis://127.0.0.1:6379/0"
    artifacts_dir: Path = REPO_ROOT / "data" / "artifacts"

    # LangGraph checkpoints share the application database by default. They hold execution
    # state only, so pointing them at a separate schema is a capacity decision, not a
    # correctness one. "memory" keeps them in the process and cannot survive a restart.
    checkpointer_backend: str = "mysql"
    checkpointer_url: str | None = None

    # Browsers treat http://localhost as a secure context, so the safe default also works
    # for local development; only a plain-HTTP LAN deployment needs to turn this off.
    session_cookie_name: str = "quickstudy_session"
    session_cookie_secure: bool = True

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("artifacts_dir")
    @classmethod
    def _anchor_to_repo_root(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @model_validator(mode="after")
    def _compose_database_url(self) -> Self:
        if not self.database_url:
            user = quote(self.mysql_user, safe="")
            password = quote(self.mysql_password, safe="")
            netloc = f"{user}:{password}@{self.mysql_host}:{self.mysql_port}"
            self.database_url = f"mysql://{netloc}/{self.mysql_database}"
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """Return ``database_url`` with an explicit DBAPI driver for SQLAlchemy.

        Compose and ``.env`` describe the database with a plain ``mysql://`` URL because
        that is what operators recognise; SQLAlchemy needs the driver named.
        """
        url = self.database_url or ""
        prefix = "mysql://"
        return f"mysql+pymysql://{url[len(prefix):]}" if url.startswith(prefix) else url


@lru_cache
def get_settings() -> Settings:
    return Settings()
