from pathlib import Path
from typing import Callable

from ptw.domain.models import Target
from ptw.storage.atomic import atomic_restore_bytes, atomic_write_pair_with_backup


def commit_single_issue(
    update_cache: Callable[[list[Target], list[str], str], bool],
    targets: list[Target],
    results: list[str],
    issue: str,
    output_path: Path,
    success_content: str,
    fail_path: Path,
    failure_content: str,
    cache_path: Path | None = None,
) -> bool:
    if cache_path is None:
        from ptw.constants import RECENT_10_CACHE_FILE

        cache_path = RECENT_10_CACHE_FILE
    original_cache = cache_path.read_bytes() if cache_path.exists() else None
    if not update_cache(targets, results, issue):
        return False
    try:
        atomic_write_pair_with_backup(
            output_path,
            success_content,
            fail_path,
            failure_content,
        )
    except Exception:
        atomic_restore_bytes(cache_path, original_cache)
        raise
    return True
