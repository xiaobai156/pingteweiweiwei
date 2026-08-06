
import re

from ptw.parsing.candidates import ParsedCandidate, direction_candidates
from ptw.parsing.primitives import issue_windows_with_issues, normalize_digit_tail


def collect_hongyun_candidates(text: str, keyword: str) -> list[ParsedCandidate]:
    title_prefix = "澳门鸿运『"
    candidates: list[ParsedCandidate] = []
    start = text.find(keyword)
    block_index = 0
    position = 0
    while start >= 0:
        end = text.find(title_prefix, start + len(keyword))
        block = text[start:] if end < 0 else text[start:end]
        for issue, window in issue_windows_with_issues(block):
            match = re.search(
                rf"(?<![0-9]){re.escape(issue)}期《黄金屋》平特尾:\[([0-9]{{1,4}})尾\]",
                window,
            )
            if not match:
                continue
            digit = normalize_digit_tail(match.group(1))
            if digit:
                candidates.append(
                    ParsedCandidate(
                        issue,
                        f"{digit}尾",
                        window,
                        block,
                        block_index,
                        position,
                    )
                )
                position += 1
        block_index += 1
        start = text.find(keyword, start + len(keyword))
    return candidates


def extract_hongyun(text: str, issue: str, keyword: str, position: str) -> str:
    candidates = direction_candidates(collect_hongyun_candidates(text, keyword), position)
    matches = [candidate for candidate in candidates if candidate.issue == issue]
    if not matches:
        return ""
    return (
        matches[-1].value
        if position in {"尾部", "tail", "bottom", "下"}
        else matches[0].value
    )
