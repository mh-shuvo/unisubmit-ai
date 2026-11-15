"""Uploaded and generated files, kept per university under the storage root.

Rows store paths relative to the root (`3/submissions/x.pdf`) and every read
goes through `resolve`, so the same rows work in Docker and natively and a
stored path can never point outside the root (DEF-042).
"""

import contextlib
import hashlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal

from .core.config import get_settings
from .pdf_utils import PDFError, inspect_pdf

Kind = Literal["guidelines", "submissions", "reports"]
MB = 1024 * 1024


class UploadRejected(ValueError):
    """The upload was not stored; the message is for the uploader."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    relative_path: str
    sha256: str
    size_bytes: int


def root() -> Path:
    return get_settings().storage_dir.resolve()


def directory(university_id: int, kind: Kind) -> Path:
    path = root() / str(university_id) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def relative(path: Path) -> str:
    return path.resolve().relative_to(root()).as_posix()


def resolve(stored: str) -> Path:
    """The file a stored path names; FileNotFoundError for anything outside the root."""
    if not stored or Path(stored).is_absolute():
        raise FileNotFoundError(stored)
    path = (root() / stored).resolve()
    if not path.is_relative_to(root()):
        raise FileNotFoundError(stored)
    return path


def relocate(stored: str, university_id: int, kind: Kind) -> str | None:
    """Move a file stored under an absolute pre-v10 path into the university's
    folder and return its relative path. None if the file cannot be found.

    `/app/storage/submissions/x.pdf` written by a container is also looked for
    at `<root>/submissions/x.pdf`, where the same volume is mounted now.
    """
    old = Path(stored)
    candidates = [old]
    if "storage" in old.parts:
        tail = old.parts[len(old.parts) - old.parts[::-1].index("storage") :]
        candidates.append(root().joinpath(*tail))
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        return None
    target = directory(university_id, kind) / source.name
    if source.resolve() != target.resolve():
        shutil.move(source, target)
    return relative(target)


def save_pdf(source: IO[bytes], university_id: int, kind: Kind, prefix: str) -> StoredFile:
    """Store an uploaded PDF under a random name, checking its signature and
    the size limit as it is copied (P3.8). A rejected or interrupted upload
    leaves no file: it is written to a `.part` file and renamed when complete.
    """
    limit = get_settings().max_upload_mb * MB
    folder = directory(university_id, kind)
    digest, size = hashlib.sha256(), 0
    part: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as target:
            part = Path(target.name)
            while chunk := source.read(MB):
                if size == 0 and not chunk.startswith(b"%PDF-"):
                    raise UploadRejected("The uploaded file is not a PDF.")
                size += len(chunk)
                if size > limit:
                    raise UploadRejected(
                        f"The file is larger than the {limit // MB} MB upload limit."
                    )
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise UploadRejected("The uploaded file is empty.")
        final = folder / f"{prefix}_{uuid.uuid4().hex}.pdf"
        part.replace(final)
    except BaseException:
        if part is not None:
            part.unlink(missing_ok=True)
        raise
    return StoredFile(relative(final), digest.hexdigest(), size)


def delete(stored: str) -> None:
    """Remove a stored file; a missing file or a path outside the root is ignored."""
    with contextlib.suppress(FileNotFoundError):
        resolve(stored).unlink(missing_ok=True)


class UploadRefused(ValueError):
    """An upload that was not accepted; the message is shown to the uploader."""


def store_pdf(
    source: IO[bytes], university_id: int, kind: Kind, prefix: str
) -> tuple[StoredFile, dict[str, Any]]:
    """Store an upload and read it; returns (StoredFile, inspect_pdf info).

    Rejects files that are not PDFs, too large, encrypted or unreadable, and
    leaves no file behind when it does (P3.8).
    """
    try:
        stored = save_pdf(source, university_id, kind, prefix)
    except UploadRejected as e:
        raise UploadRefused(str(e)) from None
    try:
        return stored, inspect_pdf(str(resolve(stored.relative_path)))
    except PDFError as e:
        delete(stored.relative_path)
        raise UploadRefused(str(e)) from None
