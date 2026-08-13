"""The domain tables. Importing this package registers every table on ``Base.metadata``."""

from app.db.base import Base
from app.db.models.corpus import Chunk, Document, Edge, Snapshot, Source, Symbol
from app.db.models.execution import Artifact, Run, Step
from app.db.models.identity import BOOTSTRAP_SLOT, User, UserSession
from app.db.models.project import Project, ProjectMember
from app.db.models.tutorial import Approval, Chapter, Citation, Claim, Outline

__all__ = [
    "BOOTSTRAP_SLOT",
    "Approval",
    "Artifact",
    "Base",
    "Chapter",
    "Chunk",
    "Citation",
    "Claim",
    "Document",
    "Edge",
    "Outline",
    "Project",
    "ProjectMember",
    "Run",
    "Snapshot",
    "Source",
    "Step",
    "Symbol",
    "User",
    "UserSession",
]
