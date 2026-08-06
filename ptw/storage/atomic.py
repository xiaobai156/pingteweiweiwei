import os
import shutil
import tempfile
import time
from pathlib import Path


def acquire_lock(path: Path, timeout: float = 30.0) -> tuple[int, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, f"{os.getpid()}\n".encode("ascii"))
            return lock_fd, lock_path
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待文件锁超时：{path}")
            time.sleep(0.05)


def release_lock(lock_fd: int, lock_path: Path) -> None:
    os.close(lock_fd)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def atomic_write_with_backup(path: Path, content: str) -> None:
    lock_fd, lock_path = acquire_lock(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = path.with_name(f"{path.name}.bak")
        backup_index = 1
        while backup_path.exists():
            backup_path = path.with_name(f"{path.name}.bak.{backup_index}")
            backup_index += 1
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                shutil.copy2(path, backup_path)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    finally:
        release_lock(lock_fd, lock_path)


def atomic_write_pair_with_backup(
    first_path: Path,
    first_content: str,
    second_path: Path,
    second_content: str,
) -> None:
    paths = (first_path, second_path)
    if first_path.resolve() == second_path.resolve():
        raise ValueError("成对原子写入不能使用同一文件")

    lock_info: list[tuple[int, Path]] = []
    temp_paths: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    contents = {first_path: first_content, second_path: second_content}
    try:
        for path in sorted(paths, key=lambda item: str(item.resolve())):
            lock_info.append(acquire_lock(path))
        for path in paths:
            originals[path] = path.read_bytes() if path.exists() else None
            temp_fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(temp_name)
            temp_paths[path] = temp_path
            with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(contents[path])
                handle.flush()
                os.fsync(handle.fileno())

        for path in paths:
            if originals[path] is not None:
                backup_path = path.with_name(f"{path.name}.bak")
                backup_index = 1
                while backup_path.exists():
                    backup_path = path.with_name(f"{path.name}.bak.{backup_index}")
                    backup_index += 1
                shutil.copy2(path, backup_path)
            os.replace(temp_paths[path], path)
    except Exception:
        for path, original in originals.items():
            if original is None:
                if path.exists() and path.is_file():
                    path.unlink()
                continue
            restore_fd, restore_name = tempfile.mkstemp(
                prefix=f".{path.name}.restore.",
                suffix=".tmp",
                dir=path.parent,
            )
            restore_path = Path(restore_name)
            try:
                with os.fdopen(restore_fd, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore_path, path)
            finally:
                if restore_path.exists():
                    restore_path.unlink()
        raise
    finally:
        for temp_path in temp_paths.values():
            if temp_path.exists():
                temp_path.unlink()
        for lock_fd, lock_path in reversed(lock_info):
            release_lock(lock_fd, lock_path)


def atomic_restore_bytes(path: Path, content: bytes | None) -> None:
    lock_fd, lock_path = acquire_lock(path)
    temp_path: Path | None = None
    try:
        if content is None:
            if path.exists() and path.is_file():
                path.unlink()
            return
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.restore.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        release_lock(lock_fd, lock_path)
