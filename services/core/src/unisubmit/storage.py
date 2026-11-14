"""Uploaded and generated files, kept per university under the storage root.

Rows store paths relative to the root (`3/submissions/x.pdf`) and every read
goes through `resolve`, so the same rows work in Docker and natively and a
stored path can never point outside the root (DEF-042).
"""

import shutil
from pathlib import Path
from typing import Literal

from .core.config import get_settings

Kind = Literal["guidelines", "submissions", "reports"]


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
