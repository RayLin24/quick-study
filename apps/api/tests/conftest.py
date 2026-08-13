"""Shared database fixtures and domain factories.

Every test in this phase runs against SQLite by default so the suite needs no external
service. Set ``QUICKSTUDY_TEST_MYSQL_URL`` to also exercise the MySQL-only behaviour
(FULLTEXT search and the real migration target); those tests skip when it is unset.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from alembic import command
from app.db.base import Base
from app.db.models import (
    Artifact,
    Chapter,
    Chunk,
    Document,
    Outline,
    Project,
    ProjectMember,
    Run,
    Snapshot,
    Source,
    Step,
    Symbol,
    User,
)
from app.db.models.enums import (
    ArtifactKind,
    CodeLanguage,
    DocumentKind,
    ProjectRole,
    RunPhase,
    SourceKind,
    SymbolKind,
)
from app.db.models.execution import unchecked_run_state
from app.settings import REPO_ROOT

MYSQL_URL_ENV = "QUICKSTUDY_TEST_MYSQL_URL"
STRONG_PASSWORD = "correct horse battery staple"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session


@pytest.fixture
def second_db(engine: Engine) -> Iterator[Session]:
    """A second session on the same database, for concurrency and lease contention tests."""
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session


@pytest.fixture(scope="session")
def mysql_url() -> str:
    url = os.environ.get(MYSQL_URL_ENV)
    if not url:
        pytest.skip(f"set {MYSQL_URL_ENV} to run the MySQL-only checks")
    return url


def alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logging"] = False
    return config


def reset_mysql_database(url: str) -> str:
    """Recreate the target database so each run starts from nothing.

    The name must contain ``test``: this drops the schema, and pointing it at a real
    deployment by accident is not a recoverable mistake.
    """
    target = sa.make_url(url)
    database = target.database
    assert database and "test" in database, f"point {MYSQL_URL_ENV} at a scratch schema"
    server = sa.create_engine(target.set(database=""), isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS `{database}`"))
            connection.execute(
                sa.text(
                    f"CREATE DATABASE `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
    finally:
        server.dispose()
    return url


@pytest.fixture
def migrated_mysql_engine(mysql_url: str) -> Iterator[Engine]:
    """A freshly migrated MySQL database.

    InnoDB only updates a FULLTEXT index at commit time, so tests that search have to
    commit their fixtures; the database is therefore rebuilt instead of rolled back.
    """
    command.upgrade(alembic_config(reset_mysql_database(mysql_url)), "head")
    engine = sa.create_engine(mysql_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def utcnow() -> datetime:
    return datetime.now(UTC)


_SEQUENCE = count(1)


def make_user(
    db: Session,
    *,
    email: str | None = None,
    password_hash: str = "$argon2id$placeholder",
    **overrides: Any,
) -> User:
    email = email or f"member{next(_SEQUENCE)}@example.test"
    user = User(email=email, display_name=email.split("@")[0], password_hash=password_hash)
    for key, value in overrides.items():
        setattr(user, key, value)
    db.add(user)
    db.flush()
    return user


def make_project(db: Session, *, owner: User | None = None, slug: str | None = None) -> Project:
    slug = slug or f"demo-{next(_SEQUENCE)}"
    owner = owner or make_user(db)
    project = Project(owner_id=owner.id, slug=slug, name=slug.title())
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.OWNER))
    db.flush()
    return project


def make_run(db: Session, *, project: Project | None = None, **overrides: Any) -> Run:
    """Build a run in any state, including ones the pipeline could not reach on its own."""
    project = project or make_project(db)
    run = Run(project_id=project.id, thread_id=f"thread-{project.slug}-{next(_SEQUENCE)}")
    with unchecked_run_state():
        for key, value in overrides.items():
            setattr(run, key, value)
    db.add(run)
    db.flush()
    return run


def make_step(db: Session, *, run: Run | None = None, name: str = "snapshot", **kw: Any) -> Step:
    run = run or make_run(db)
    step = Step(
        run_id=run.id,
        project_id=run.project_id,
        name=name,
        phase=RunPhase.SNAPSHOT,
        idempotency_key=f"{run.id}:{name}:{next(_SEQUENCE)}",
        **kw,
    )
    db.add(step)
    db.flush()
    return step


def make_artifact(
    db: Session,
    *,
    project: Project | None = None,
    run: Run | None = None,
    step: Step | None = None,
    kind: ArtifactKind = ArtifactKind.RAW_HTML,
) -> Artifact:
    project = project or (run.project if run is not None else None) or make_project(db)
    digest = f"{next(_SEQUENCE):064x}"
    artifact = Artifact(
        project_id=project.id,
        run_id=run.id if run else None,
        step_id=step.id if step else None,
        kind=kind,
        sha256=digest,
        storage_path=f"{digest[:2]}/{digest[2:4]}/{digest}",
    )
    db.add(artifact)
    db.flush()
    return artifact


def make_outline(db: Session, *, run: Run | None = None, version: int = 1) -> Outline:
    run = run or make_run(db)
    outline = Outline(project_id=run.project_id, run_id=run.id, version=version)
    db.add(outline)
    db.flush()
    return outline


def make_chapter(db: Session, *, outline: Outline | None = None, ordinal: int = 0) -> Chapter:
    outline = outline or make_outline(db)
    chapter = Chapter(
        project_id=outline.project_id,
        outline_id=outline.id,
        ordinal=ordinal,
        slug=f"chapter-{ordinal}-{next(_SEQUENCE)}",
        title=f"Chapter {ordinal}",
    )
    db.add(chapter)
    db.flush()
    return chapter


def make_snapshot(
    db: Session,
    *,
    project: Project | None = None,
    locator: str = "https://docs.example.test/",
) -> Snapshot:
    project = project or make_project(db)
    source = Source(
        project_id=project.id,
        kind=SourceKind.WEBSITE,
        locator=locator,
        locator_fingerprint=f"{abs(hash(locator)):064x}"[:64],
    )
    db.add(source)
    db.flush()
    snapshot = Snapshot(project_id=project.id, source_id=source.id, fingerprint="b" * 64)
    db.add(snapshot)
    db.flush()
    return snapshot


def make_document(
    db: Session,
    *,
    snapshot: Snapshot | None = None,
    title: str = "Deployment guide",
    body_text: str = "Deploy the gateway service with the supervisor process.",
    uri: str = "https://docs.example.test/deploy",
    **overrides: Any,
) -> Document:
    snapshot = snapshot or make_snapshot(db)
    document = Document(
        project_id=snapshot.project_id,
        snapshot_id=snapshot.id,
        source_id=snapshot.source_id,
        kind=DocumentKind.WEB_PAGE,
        uri=uri,
        uri_fingerprint=f"{abs(hash(uri)):064x}"[:64],
        path="/deploy",
        title=title,
        body_text=body_text,
    )
    for key, value in overrides.items():
        setattr(document, key, value)
    db.add(document)
    db.flush()
    return document


def make_chunk(
    db: Session,
    *,
    document: Document,
    ordinal: int = 0,
    text: str = "Deploy the gateway service with the supervisor process.",
) -> Chunk:
    chunk = Chunk(
        project_id=document.project_id,
        document_id=document.id,
        ordinal=ordinal,
        heading_path="Deployment",
        text=text,
        char_start=0,
        char_end=len(text),
        token_count=len(text.split()),
        sha256="c" * 64,
    )
    db.add(chunk)
    db.flush()
    return chunk


def make_symbol(
    db: Session,
    *,
    document: Document,
    name: str = "build_gateway",
    qualified_name: str = "gateway.factory.build_gateway",
    signature: str = "def build_gateway(config: Config) -> Gateway",
) -> Symbol:
    symbol = Symbol(
        project_id=document.project_id,
        document_id=document.id,
        kind=SymbolKind.FUNCTION,
        language=CodeLanguage.PYTHON,
        name=name,
        qualified_name=qualified_name,
        signature=signature,
        start_line=10,
        end_line=24,
    )
    db.add(symbol)
    db.flush()
    return symbol


def in_minutes(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


@dataclass(frozen=True)
class SeededCorpus:
    """A small searchable corpus shared by the retrieval tests.

    ``supervisor`` deliberately appears in most documents: MySQL's natural-language mode
    ignores terms present in over half the rows, so a search that still finds it proves
    the implementation uses boolean mode.
    """

    project: Project
    other_project: Project
    primary_snapshot: Snapshot
    secondary_snapshot: Snapshot
    deployment_document: Document
    configuration_document: Document
    legacy_document: Document
    chinese_document: Document
    other_project_document: Document
    symbol: Symbol


def seed_corpus(db: Session) -> SeededCorpus:
    project = make_project(db, slug="alpha")
    other_project = make_project(db, slug="beta")
    primary = make_snapshot(db, project=project, locator="https://docs.example.test/")
    secondary = make_snapshot(db, project=project, locator="https://legacy.example.test/")
    other_snapshot = make_snapshot(db, project=other_project, locator="https://docs.example.test/")

    deployment = make_document(
        db,
        snapshot=primary,
        title="Gateway deployment guide",
        body_text="Deploy the gateway service behind the supervisor process.",
        uri="https://docs.example.test/deploy",
    )
    make_chunk(db, document=deployment, text=deployment.body_text)
    configuration = make_document(
        db,
        snapshot=primary,
        title="Configuration reference",
        body_text="The supervisor reads gateway settings from the configuration file.",
        uri="https://docs.example.test/configuration",
    )
    make_chunk(db, document=configuration, text=configuration.body_text)
    legacy = make_document(
        db,
        snapshot=secondary,
        title="Legacy supervisor notes",
        body_text="The legacy supervisor predates the gateway rewrite entirely.",
        uri="https://legacy.example.test/notes",
    )
    make_chunk(db, document=legacy, text=legacy.body_text)
    # Written without spaces, as Chinese is: the built-in parser makes one token of the
    # whole sentence, so nothing here is findable until the ngram parser is in place.
    chinese = make_document(
        db,
        snapshot=primary,
        title="网关部署指南",
        body_text="在监督进程后面部署网关服务，并配置反向代理。",
        uri="https://docs.example.test/zh/deploy",
    )
    make_chunk(db, document=chinese, text=chinese.body_text)
    other_document = make_document(
        db,
        snapshot=other_snapshot,
        title="Gateway deployment guide",
        body_text="Deploy the gateway service behind the supervisor process.",
        uri="https://docs.example.test/deploy",
    )
    make_chunk(db, document=other_document, text=other_document.body_text)

    repo_file = make_document(
        db,
        snapshot=primary,
        title="factory.py",
        body_text="def build_gateway(config): return Gateway(config)",
        uri="https://docs.example.test/src/gateway/factory.py",
        kind=DocumentKind.REPO_FILE,
        path="src/gateway/factory.py",
        code_language=CodeLanguage.PYTHON,
    )
    symbol = make_symbol(db, document=repo_file)
    db.commit()
    return SeededCorpus(
        project=project,
        other_project=other_project,
        primary_snapshot=primary,
        secondary_snapshot=secondary,
        deployment_document=deployment,
        configuration_document=configuration,
        legacy_document=legacy,
        chinese_document=chinese,
        other_project_document=other_document,
        symbol=symbol,
    )
