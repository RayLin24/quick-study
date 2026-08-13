"""The controlled vocabularies stored in the domain tables.

Values are the lowercase strings persisted in MySQL; renaming one is a data migration.
"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class ProjectRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReaderLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class LengthPreset(StrEnum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


class SourceKind(StrEnum):
    WEBSITE = "website"
    GITHUB_REPO = "github_repo"


class SnapshotStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    READY = "ready"
    FAILED = "failed"


class DocumentKind(StrEnum):
    WEB_PAGE = "web_page"
    REPO_FILE = "repo_file"


class CodeLanguage(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    OTHER = "other"


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"


class EdgeKind(StrEnum):
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    REFERENCES = "references"
    CONTAINS = "contains"


class RunStatus(StrEnum):
    """How a run is executing. It never says *what* the run is doing; that is the phase."""

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunPhase(StrEnum):
    """Which stage of the generation graph a run has reached."""

    QUEUED = "queued"
    DISCOVER = "discover"
    SNAPSHOT = "snapshot"
    PARSE = "parse"
    INDEX = "index"
    ANALYZE = "analyze"
    OUTLINE = "outline"
    HUMAN_INTERRUPT = "human_interrupt"
    CHAPTERS = "chapters"
    DIAGRAMS = "diagrams"
    VALIDATE = "validate"
    PUBLISH = "publish"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactKind(StrEnum):
    RAW_HTML = "raw_html"
    RAW_REPO_FILE = "raw_repo_file"
    NORMALIZED_CORPUS = "normalized_corpus"
    SNAPSHOT_MANIFEST = "snapshot_manifest"
    EVIDENCE_PACK = "evidence_pack"
    CHAPTER_MARKDOWN = "chapter_markdown"
    DIAGRAM_SVG = "diagram_svg"
    EXPORT_BUNDLE = "export_bundle"


class OutlineStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ChapterStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    DRAFTED = "drafted"
    VALIDATED = "validated"
    LOCKED = "locked"
    FAILED = "failed"
    PUBLISHED = "published"


class ClaimKind(StrEnum):
    FACT = "fact"
    API_SIGNATURE = "api_signature"
    BEHAVIOUR = "behaviour"
    TEACHING_ABSTRACTION = "teaching_abstraction"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CitationKind(StrEnum):
    WEB = "web"
    REPO = "repo"


class ApprovalSubject(StrEnum):
    SOURCE_SCOPE = "source_scope"
    OUTLINE = "outline"
    CHAPTER = "chapter"
    PUBLICATION = "publication"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
