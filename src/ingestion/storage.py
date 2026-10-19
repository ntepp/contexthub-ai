"""Original files on disk.

Files are stored as ``<UPLOAD_DIR>/<collection_id>/<document_id><ext>``: the
client-supplied filename is display metadata only and never touches the filesystem.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from src.config import get_settings

_COPY_BLOCK = 1024 * 1024


class FileTooLarge(Exception):
    pass


@dataclass
class StagedFile:
    path: Path
    size: int
    sha256: str


def upload_root() -> Path:
    root = get_settings().upload_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def stage_stream(stream: BinaryIO, max_bytes: int) -> StagedFile:
    """Copy an upload to a temp file inside the upload root, hashing as we go."""
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(prefix=".staging-", dir=upload_root())
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                block = stream.read(_COPY_BLOCK)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise FileTooLarge()
                digest.update(block)
                out.write(block)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return StagedFile(path=Path(tmp_name), size=size, sha256=digest.hexdigest())


def stage_bytes(content: bytes) -> StagedFile:
    fd, tmp_name = tempfile.mkstemp(prefix=".staging-", dir=upload_root())
    with os.fdopen(fd, "wb") as out:
        out.write(content)
    return StagedFile(path=Path(tmp_name), size=len(content), sha256=hashlib.sha256(content).hexdigest())


def final_path(collection_id: uuid.UUID, document_id: uuid.UUID, extension: str) -> Path:
    directory = upload_root() / str(collection_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{document_id}{extension}"


def commit_staged(staged: StagedFile, destination: Path) -> Path:
    shutil.move(str(staged.path), destination)
    return destination


def resolve_stored(storage_path: str | None) -> Path | None:
    """Return the stored file if it exists *and* lives under the upload root."""
    if not storage_path:
        return None
    root = upload_root()
    candidate = Path(storage_path)
    candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def relative_to_root(path: Path) -> str:
    return path.resolve().relative_to(upload_root()).as_posix()


def delete_stored(storage_path: str | None) -> None:
    path = resolve_stored(storage_path)
    if path is not None:
        path.unlink(missing_ok=True)


def delete_collection_dir(collection_id: uuid.UUID) -> None:
    directory = (upload_root() / str(collection_id)).resolve()
    if upload_root() in directory.parents and directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
