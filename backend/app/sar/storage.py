"""The SAR file cache: scene crops and comparison images on disk, under one root.

Paths are built only from ids and digests, never from client input, and every path is checked to
resolve inside the root before it is read, written or deleted (AGENTS.md §12.5). The cache is
ephemeral by design: a container restart wipes it, and past ``max_bytes`` the least recently used
entries are removed; a comparison whose images are gone reads as expired and can be recomputed.
"""

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Literal

#: The three images of a comparison.
ProductKind = Literal["before", "after", "change"]
#: Every product kind, in a fixed order.
PRODUCT_KINDS: tuple[ProductKind, ...] = ("before", "after", "change")
#: Bumped when the crop file layout changes, so old crops aren't reused.
CROP_VERSION = 1
#: Touching a cache entry more often than this does nothing.
_TOUCH_EVERY_S = 60.0


class SarStorage:
    """Crops (``crops/<digest>.tif``) and products (``comparisons/<id>/<generation>/*.png``)."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.max_bytes = max_bytes
        (self.root / "crops").mkdir(exist_ok=True)
        (self.root / "comparisons").mkdir(exist_ok=True)

    def guard(self, path: Path) -> Path:
        """``path`` resolved, if it is inside the root; else ``ValueError``."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("path outside the SAR storage root")
        return resolved

    def crop_path(self, stac_id: str, polarization: str, grid_key: str) -> Path:
        """Where a scene's crop for one grid and polarization is cached."""
        digest = hashlib.sha256(
            f"{stac_id}|{polarization}|{grid_key}|v{CROP_VERSION}".encode()
        ).hexdigest()[:40]
        return self.guard(self.root / "crops" / f"{digest}.tif")

    def products_dir(self, comparison_id: int, generation: int) -> Path:
        """The directory of one run's images."""
        return self.guard(
            self.root / "comparisons" / str(int(comparison_id)) / str(int(generation))
        )

    def product(self, comparison_id: int, generation: int, kind: ProductKind) -> Path:
        """One image of a run."""
        if kind not in PRODUCT_KINDS:
            raise ValueError("unknown product")
        return self.guard(self.products_dir(comparison_id, generation) / f"{kind}.png")

    def has_products(self, comparison_id: int, generation: int) -> bool:
        """Whether all three images of a run are on disk."""
        return all(self.product(comparison_id, generation, k).is_file() for k in PRODUCT_KINDS)

    def touch(self, path: Path) -> None:
        """Mark an entry as just used (its mtime), at most once a minute."""
        try:
            path = self.guard(path)
            if time.time() - path.stat().st_mtime > _TOUCH_EVERY_S:
                os.utime(path)
                if path.parent.parent.parent == self.root / "comparisons":
                    os.utime(path.parent)
        except (OSError, ValueError):
            pass

    def _entries(self) -> list[tuple[float, int, Path, int | None]]:
        """(mtime, bytes, path, comparison id or None) for every crop file and product run."""
        entries: list[tuple[float, int, Path, int | None]] = []
        for crop in (self.root / "crops").glob("*.tif"):
            try:
                stat = crop.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, crop, None))
        for comparison in (self.root / "comparisons").iterdir():
            if not comparison.is_dir() or not comparison.name.isdigit():
                continue
            for run in comparison.iterdir():
                if not run.is_dir() or not run.name.isdigit():
                    continue
                files = [f for f in run.iterdir() if f.is_file()]
                size = sum(f.stat().st_size for f in files)
                mtime = max([run.stat().st_mtime, *(f.stat().st_mtime for f in files)])
                entries.append((mtime, size, run, int(comparison.name)))
        return entries

    def usage(self) -> int:
        """Bytes used by crops and products."""
        return sum(size for _, size, _, _ in self._entries())

    def evict(
        self, reserve_bytes: int = 0, pinned: set[Path] | frozenset[Path] = frozenset()
    ) -> set[int]:
        """Delete the least recently used entries until ``usage + reserve`` fits the cap.

        Returns the ids of comparisons that lost their images. ``pinned`` paths are kept.
        """
        entries = sorted(self._entries())
        used = sum(size for _, size, _, _ in entries)
        evicted: set[int] = set()
        keep = {self.guard(p) for p in pinned if p.exists()}
        for _, size, path, comparison_id in entries:
            if used + reserve_bytes <= self.max_bytes:
                break
            if path.resolve() in keep:
                continue
            self._remove(path)
            used -= size
            if comparison_id is not None:
                evicted.add(comparison_id)
        return evicted

    def remove_comparison(self, comparison_id: int) -> None:
        """Delete every run of a comparison."""
        self._remove(self.root / "comparisons" / str(int(comparison_id)))

    def remove_orphans(self, known_ids: set[int]) -> int:
        """Delete product directories of comparisons that no longer exist; returns how many."""
        removed = 0
        for comparison in (self.root / "comparisons").iterdir():
            if comparison.name.isdigit() and int(comparison.name) not in known_ids:
                self._remove(comparison)
                removed += 1
        return removed

    def remove_tmp(self) -> None:
        """Delete half-written files and directories left by an interrupted worker."""
        for tmp in list(self.root.rglob("*.tmp*")):
            self._remove(tmp)

    def _remove(self, path: Path) -> None:
        try:
            target = self.guard(path)
        except ValueError:
            return
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
