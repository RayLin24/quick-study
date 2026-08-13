"""The container a page is rendered inside.

Expressed as a value rather than a shell string so the isolation is something a test can
read: every flag here is a property somebody can assert, and building the command as an
argument vector means a hostile URL is an argument and never a shell fragment.

What the container does *not* get is the point. No host mount, no application environment,
no writable filesystem outside a small tmpfs, no capabilities, and no ability to resolve
names for itself — the address the SSRF guard cleared is pinned with ``--add-host``, so
the container connects to the machine that was checked or to nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.ingestion.web.urls import CanonicalUrl

#: nobody:nogroup. The renderer needs no identity on the host.
UNPRIVILEGED_USER: Final = "65534:65534"

DEFAULT_IMAGE: Final = "quick-study-renderer:local"
DEFAULT_EGRESS_NETWORK: Final = "quick-study-egress"

_FORBIDDEN_NETWORKS: Final[frozenset[str]] = frozenset({"host", "bridge", "none", "container"})


class SandboxSpecError(ValueError):
    """Raised when a container would not actually be isolated."""


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Everything needed to start one renderer container, and nothing else."""

    image: str
    egress_network: str
    target: CanonicalUrl
    #: Addresses the SSRF guard already cleared for ``target``. Never empty in practice.
    addresses: tuple[str, ...]
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 256
    timeout_seconds: float = 20.0
    user: str = UNPRIVILEGED_USER
    tmpfs_size: str = "64m"

    def docker_command(self) -> tuple[str, ...]:
        """Return the argument vector that starts this container."""
        self._validate()
        return (
            "docker",
            "run",
            "--rm",
            "--init",
            "--read-only",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={self.tmpfs_size}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={self.user}",
            f"--network={self.egress_network}",
            f"--add-host={self.target.host}:{self.addresses[0]}",
            f"--memory={self.memory}",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}",
            f"--env=RENDER_TIMEOUT_MS={int(self.timeout_seconds * 1000)}",
            self.image,
            str(self.target),
        )

    def _validate(self) -> None:
        if self.target.scheme not in ("http", "https"):
            raise SandboxSpecError(f"{self.target.scheme!r} is not a renderable scheme")
        if not self.addresses:
            raise SandboxSpecError(
                "the target has no cleared address; the SSRF guard runs before the browser"
            )
        if not self.egress_network:
            raise SandboxSpecError("a renderer needs a named egress network")
        if self.egress_network in _FORBIDDEN_NETWORKS:
            raise SandboxSpecError(
                f"{self.egress_network!r} would give the renderer the host's network view"
            )
        if not self.image:
            raise SandboxSpecError("a renderer needs an image")
