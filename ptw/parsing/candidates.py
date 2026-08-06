from dataclasses import dataclass

from ptw.domain.models import Target
from ptw.parsing.primitives import issue_near_lines, issue_windows_with_issues
from ptw.parsing.registry import extract_value_by_mode


@dataclass(frozen=True)
class ParsedCandidate:
    issue: str
    value: str
    window: str
    block: str
    block_index: int
    position: int


def collect_candidates(blocks: list[str], target: Target) -> list[ParsedCandidate]:
    candidates: list[ParsedCandidate] = []
    position = 0
    for block_index, block in enumerate(blocks):
        for issue, raw_window in issue_windows_with_issues(block):
            windows = (
                issue_near_lines(raw_window, issue)
                if target.mode == "tail_after_issue"
                else [raw_window]
            )
            for window in windows:
                value = extract_value_by_mode(window, issue, target)
                if value:
                    candidates.append(
                        ParsedCandidate(issue, value, window, block, block_index, position)
                    )
                    position += 1
    return candidates


def direction_candidates(
    candidates: list[ParsedCandidate], position: str, limit: int = 3
) -> list[ParsedCandidate]:
    ordered = sorted(candidates, key=lambda item: item.position)
    if position in {"尾部", "tail", "bottom", "下"}:
        representatives: dict[tuple[str, str], ParsedCandidate] = {}
        for candidate in reversed(ordered):
            representatives.setdefault((candidate.issue, candidate.value), candidate)
        unique = sorted(representatives.values(), key=lambda item: item.position)
        return unique[-limit:]
    representatives: dict[tuple[str, str], ParsedCandidate] = {}
    for candidate in ordered:
        representatives.setdefault((candidate.issue, candidate.value), candidate)
    return list(representatives.values())[:limit]


def direction_candidate_occurrences(
    candidates: list[ParsedCandidate], selected: list[ParsedCandidate]
) -> list[ParsedCandidate]:
    selected_keys = {(candidate.issue, candidate.value) for candidate in selected}
    return [
        candidate
        for candidate in sorted(candidates, key=lambda item: item.position)
        if (candidate.issue, candidate.value) in selected_keys
    ]
