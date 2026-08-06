import re

from ptw.domain.models import CandidateEvidence


def make_candidate_evidence(
    issue: str,
    value: str,
    anchor_text: str,
    raw_window: str,
    block: str,
    block_index: int,
    source_text: str,
    candidate_position: int,
    record_id: str = "",
    record_path: str = "",
) -> CandidateEvidence:
    lines = tuple(line for line in raw_window.splitlines() if line.strip())
    issue_line = next(
        (
            line
            for line in lines
            if re.search(rf"(?<![0-9]){re.escape(issue)}期", line.replace(" ", ""))
        ),
        lines[0] if lines else "",
    )
    value_lines = tuple(
        line
        for line in lines
        if "尾" in line or re.search(r"[【\[《(（]", line)
    ) or lines
    block_start = source_text.find(block)
    anchor_position = block.find(anchor_text) if anchor_text else -1
    return CandidateEvidence(
        issue=issue,
        value=value,
        anchor_text=anchor_text,
        raw_issue_line=issue_line,
        raw_value_lines=value_lines,
        block_id=f"block-{block_index}",
        block_start=block_start,
        block_end=block_start + len(block) if block_start >= 0 else -1,
        anchor_position=anchor_position,
        candidate_position=candidate_position,
        page_position=(
            block_start + anchor_position
            if block_start >= 0 and anchor_position >= 0
            else block_start
        ),
        block_position=block_index,
        record_id=record_id,
        record_path=record_path,
    )
