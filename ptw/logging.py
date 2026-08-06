import sys


_PROGRESS_PREFIX = "[进度 "
_progress_line_length = 0


def finish_progress() -> None:
    """End an active in-place progress line before normal terminal output."""
    global _progress_line_length
    if _progress_line_length:
        sys.stdout.write("\n")
        sys.stdout.flush()
        _progress_line_length = 0


def log(message: str) -> None:
    """Write normal messages or refresh progress messages on one terminal line."""
    global _progress_line_length
    if message.startswith(_PROGRESS_PREFIX):
        padding = max(_progress_line_length - len(message), 0)
        sys.stdout.write(f"\r{message}{' ' * padding}")
        sys.stdout.flush()
        _progress_line_length = max(_progress_line_length, len(message))
        return

    finish_progress()
    print(message, flush=True)
