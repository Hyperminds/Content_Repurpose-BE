"""Restricted media workspace for Hermes (Phase 12).

Hermes must only ever touch media for the CURRENT publishing job, inside a
temporary, isolated directory — never arbitrary filesystem paths. This module:

  - validates media descriptors (MIME/extension/size)
  - rejects path traversal and any user-controlled/absolute system paths
  - provides a per-operation temporary workspace that is cleaned up afterward

It performs NO network fetching itself (SSRF is handled by url_safety at the
fetch boundary). It governs only what lands in, and is read from, the workspace.
"""

import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from app.social_publishing.providers.hermes.constants import (
    ALLOWED_MEDIA_TYPES,
    HERMES_MAX_MEDIA_BYTES,
)

# A safe, generated filename inside the workspace (never derived from user input).
_SAFE_NAME_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9]+$")


class MediaValidationError(Exception):
    """Raised when a media descriptor is rejected before it enters the workspace."""


@dataclass(frozen=True)
class MediaDescriptor:
    """A validated piece of media to be placed in the restricted workspace."""

    extension: str
    mime_type: str
    size_bytes: int


def validate_media(extension: str, size_bytes: int) -> MediaDescriptor:
    """
    Validate a media item by extension + size. Returns a MediaDescriptor.

    Raises MediaValidationError for unsupported types or oversize files.
    """
    ext = (extension or "").lower().lstrip(".")
    mime = ALLOWED_MEDIA_TYPES.get(ext)
    if mime is None:
        raise MediaValidationError(
            f"Unsupported media type '.{ext}'. Allowed: "
            f"{', '.join(sorted(ALLOWED_MEDIA_TYPES))}"
        )
    if size_bytes <= 0:
        raise MediaValidationError("Empty media file")
    if size_bytes > HERMES_MAX_MEDIA_BYTES:
        raise MediaValidationError(
            f"Media exceeds size limit ({size_bytes} > {HERMES_MAX_MEDIA_BYTES} bytes)"
        )
    return MediaDescriptor(extension=ext, mime_type=mime, size_bytes=size_bytes)


def is_safe_workspace_name(name: str) -> bool:
    """
    True only for a simple generated filename (no path separators, no traversal).

    Rejects absolute paths, '..', drive letters, and any separator — so a
    user-controlled or crafted path can never escape the workspace.
    """
    if not name or not _SAFE_NAME_RE.match(name):
        return False
    if "/" in name or "\\" in name or ".." in name or os.path.isabs(name):
        return False
    return True


def resolve_within(workspace_dir: str, name: str) -> str:
    """
    Resolve a filename strictly inside the workspace, or raise.

    Guards against path traversal: the resolved real path must remain under the
    workspace root even if the runtime resolves symlinks.
    """
    if not is_safe_workspace_name(name):
        raise MediaValidationError(f"Unsafe media filename: {name!r}")

    root = os.path.realpath(workspace_dir)
    candidate = os.path.realpath(os.path.join(root, name))
    # candidate must be root/<name> — i.e. inside root, not root itself.
    # commonpath raises ValueError across drives (Windows); treat as unsafe.
    try:
        inside = os.path.commonpath([root, candidate]) == root
    except ValueError:
        inside = False
    if not inside or candidate == root:
        raise MediaValidationError("Resolved media path escapes the workspace")
    return candidate


@contextmanager
def media_workspace(operation_id: str) -> Iterator[str]:
    """
    Create a temporary, isolated workspace for one operation and clean it up.

    The directory name includes a sanitized operation id purely for debugging;
    the actual location is a fresh OS temp dir. Everything is removed on exit,
    even on error.
    """
    safe_op = re.sub(r"[^a-zA-Z0-9_]", "", operation_id)[:40] or "op"
    workspace = tempfile.mkdtemp(prefix=f"hermes_{safe_op}_")
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def safe_media_filename(index: int, descriptor: MediaDescriptor) -> str:
    """Generate a safe, non-user-derived filename for a validated media item."""
    return f"media_{index}.{descriptor.extension}"
