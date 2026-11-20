"""Content-addressed file storage: `$DATA_DIR/files/<sha256>` (ADR 0001, ADR 0002).

Bytes are hashed while they are written to a temporary file, which is then moved into place in one step. The same
content is stored once, however many tenders or portals reference it.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

MiB = 1024 * 1024
MAX_FILE_BYTES = 250 * MiB  # the contract allows leaving out files over 250 MB, ZIPs included
CHUNK_BYTES = 256 * 1024


def files_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "files"


class TooLarge(Exception):
    """The file passed the size limit while it was being written."""


@dataclass
class StoredBlob:
    sha256: str
    size_bytes: int
    path: Path


class BlobWriter:
    """Write a file in chunks; `commit()` moves it to its content address, `discard()` drops it."""

    def __init__(self, store: FileStore, max_bytes: int):
        self._store = store
        self._max = max_bytes
        self._hash = hashlib.sha256()
        self.size = 0
        self.tmp_path = store.tmp_dir / uuid.uuid4().hex
        self._fh = open(self.tmp_path, "wb")  # noqa: SIM115 - closed in commit/discard

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self._max:
            raise TooLarge(f"larger than {self._max // MiB} MB")
        self._hash.update(chunk)
        self._fh.write(chunk)

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def commit(self) -> StoredBlob:
        self.close()
        sha = self._hash.hexdigest()
        target = self._store.path_for(sha)
        if target.exists():
            self.tmp_path.unlink(missing_ok=True)  # already stored: identical bytes
        else:
            os.replace(self.tmp_path, target)
        return StoredBlob(sha, self.size, target)

    def discard(self) -> None:
        self.close()
        self.tmp_path.unlink(missing_ok=True)


class FileStore:
    def __init__(self, root: Path | None = None):
        self.root = root or files_dir()
        self.tmp_dir = self.root / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256

    def writer(self, max_bytes: int = MAX_FILE_BYTES) -> BlobWriter:
        return BlobWriter(self, max_bytes)

    def clean_tmp(self) -> int:
        """Remove temporary files left by a crash. Returns how many."""
        removed = 0
        for p in self.tmp_dir.iterdir():
            p.unlink(missing_ok=True)
            removed += 1
        return removed
