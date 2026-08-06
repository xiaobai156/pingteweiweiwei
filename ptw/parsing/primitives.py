
import re

from ptw.text import normalize_digits, normalize_search


def is_foreign_heading(line: str, keywords: tuple[str, ...] = ()) -> bool:
    compact = re.sub(r"\s+", "", line)
    if not compact or re.search(r"[0-9]+期|平特|平尾|特尾|开\s*[:：]|尾", compact):
        return False
    if ":" in compact or "：" in compact:
        return False
    normalized = normalize_search(compact)
    keyword_norms = tuple(normalize_search(item) for item in keywords if item)
    if any(item and (normalized in item or item in normalized) for item in keyword_norms):
        return False
    return any(
        marker in compact
        for marker in ("栏目", "模块", "作者", "论坛", "资料", "专区", "主题", "广告", "声明")
    )


def pick_blocks(
    text: str,
    keyword: str,
    peer_keywords: tuple[str, ...] = (),
    mode: str = "",
) -> list[str]:
    if not keyword:
        return [text]
    peers = tuple(dict.fromkeys((*peer_keywords, keyword)))
    def trim_block(block: str) -> str:
        end_positions = [
            pos for marker in ("\ndocument.writeln", "\n上一篇", "\n下一篇")
            if (pos := block.find(marker)) > 0
        ]
        if end_positions:
            block = block[: min(end_positions)]
        lines = block.splitlines()
        for index, line in enumerate(lines[1:], start=1):
            if is_foreign_heading(line, peers):
                return "\n".join(lines[:index])
        return block

    events: list[tuple[int, str]] = []
    for peer in peers:
        if not peer:
            continue
        start = text.find(peer)
        while start >= 0:
            line_start = text.rfind("\n", 0, start) + 1
            events.append((line_start, peer))
            start = text.find(peer, start + len(peer))
    events = sorted(set(events), key=lambda item: item[0])
    target_starts = [start for start, peer in events if peer == keyword]

    def is_complete_target_segment(segment: str) -> bool:
        has_issue = bool(re.search(r"(?<![0-9])[0-9]+期", segment))
        if mode == "open_number_tail":
            has_value = bool(re.search(r"开\s*[:：]\s*[0-9]{1,2}", segment))
        else:
            has_value = bool(
                re.search(
                    r"平特|平尾|特尾|精平|[0-9]尾|[+＋][0-9]{1,2}\s*=\s*[0-9]|[【\[]\d[】\]]",
                    segment,
                )
            )
        return has_issue and has_value

    blocks: list[str] = []
    for start in target_starts:
        end = len(text)
        for boundary, peer in events:
            if boundary <= start:
                continue
            if peer != keyword or is_complete_target_segment(text[start:boundary]):
                end = boundary
                break
        blocks.append(trim_block(text[start:end]))
    return [block for block in blocks if block]


def pick_block(text: str, keyword: str, position: str) -> str:
    blocks = pick_blocks(text, keyword)
    if not blocks:
        return ""
    return blocks[-1] if position in {"尾部", "tail", "bottom", "下"} else blocks[0]


def issue_window(text: str, issue: str) -> str:
    windows = issue_windows(text, issue)
    return windows[0] if windows else ""


def issue_windows_with_issues(text: str) -> list[tuple[str, str]]:
    lines = normalize_digits(text).splitlines()
    marker_pattern = re.compile(r"(?<![0-9])[0-9]+期")
    markers: list[tuple[int, int, str]] = []

    def raw_index(line: str, compact_index: int) -> int:
        visible = 0
        for index, char in enumerate(line):
            if char == " ":
                continue
            if visible == compact_index:
                return index
            visible += 1
        return len(line)

    for line_index, line in enumerate(lines):
        compact = line.replace(" ", "")
        for match in marker_pattern.finditer(compact):
            markers.append(
                (line_index, raw_index(line, match.start()), normalize_digits(match.group(0)[:-1]))
            )

    windows: list[tuple[str, str]] = []
    for marker_index, (line_index, marker_start, issue) in enumerate(markers):
        next_marker = markers[marker_index + 1] if marker_index + 1 < len(markers) else None
        if next_marker and next_marker[0] == line_index:
            current_line = lines[line_index][marker_start : next_marker[1]]
            end = line_index + 1
        else:
            current_line = lines[line_index][marker_start:]
            end = next_marker[0] if next_marker else len(lines)
        if marker_index == 0 or markers[marker_index - 1][0] != line_index:
            current_line = lines[line_index][:marker_start] + current_line
        windows.append((issue, "\n".join([current_line, *lines[line_index + 1 : end]])))
    return windows


def issue_windows(text: str, issue: str) -> list[str]:
    wanted = normalize_digits(issue)
    return [
        window
        for candidate_issue, window in issue_windows_with_issues(text)
        if candidate_issue == wanted
    ]


def issue_near_lines(text: str, issue: str) -> list[str]:
    issue = normalize_digits(issue)
    marker_pattern = re.compile(rf"(?<![0-9]){re.escape(issue)}期")
    windows: list[str] = []
    for window in issue_windows(text, issue):
        near = window.splitlines()[:3]
        filtered = [
            item
            for item in near
            if marker_pattern.search(item.replace(" ", "")) or re.search(r"T\d|[+=]\d|平特|平尾", item)
        ]
        if filtered:
            windows.append("\n".join(filtered))
    return windows


def normalize_digit_tail(value: str) -> str:
    value = normalize_digits(value)
    if re.search(r"[.\-]", value):
        numbers = re.findall(r"[0-9]+", value)
        if len(numbers) > 1:
            tails = {number[-1] for number in numbers}
            return tails.pop() if len(tails) == 1 else ""
    digits = re.findall(r"\d", value)
    if not digits:
        return ""
    unique = set(digits)
    if len(unique) == 1:
        return digits[0]
    return ""


def extract_tail(window: str) -> str:
    compact = re.sub(r"\s+", "/", window)

    patterns = [
        r"平特[（(](\d)[）)]尾",
        r"平特[一㈠1]尾[^0-9]{0,20}([0-9]{1,4})",
        r"平特一尾[)）】╣]*([0-9]{1,4})",
        r"[【\[\(（«<《〖{┣╒『]([0-9.\-\s]+)尾?[】\]\)）»>》〗}┫╕』]",
        r"([0-9]{1,4})尾",
        r"平尾([0-9])",
        r"[+=](\d)尾",
        r"[【\[\(（《〖{┣╒『]([0-9])(?![0-9])[】\]\)）》〗}┫╕』]",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            digit = normalize_digit_tail(match.group(1))
            if digit:
                return f"{digit}尾"
    return ""


def extract_phrase(window: str) -> str:
    compact = re.sub(r"\s+", "", window)
    match = re.search(r"[【\[]([^】\]]+?)(?:平特开|开)[】\]]", compact)
    if not match:
        match = re.search(r"[【\[]([^】\]]+)[】\]]", compact)
    if not match:
        return ""
    value = re.sub(r"(?:平特开|平特|开)$", "", match.group(1)).strip()
    return value


def extract_shenqi_macau(block: str, issue: str) -> str:
    marker = f"{issue}期"
    for line in block.splitlines():
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        match = re.search(rf"{re.escape(marker)}平尾[\[【(（](\d)[\]】)）]", compact)
        if match:
            return f"{match.group(1)}尾"
    return ""


def extract_xingyun(block: str, issue: str) -> str:
    lines = block.splitlines()
    marker = f"{issue}期"
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        same_line = re.search(r"(\d{1,4})尾再次爆中吗[?？]", compact)
        if same_line:
            digit = normalize_digit_tail(same_line.group(1))
            if digit:
                return f"{digit}尾"
        formal = re.search(r"[+=](\d)尾", compact)
        if formal:
            return f"{formal.group(1)}尾"
        if index + 1 < len(lines):
            next_line = re.sub(r"\s+", "", lines[index + 1])
            next_match = re.search(r"(\d{1,4})尾再次爆中吗[?？]", next_line)
            if next_match:
                digit = normalize_digit_tail(next_match.group(1))
                if digit:
                    return f"{digit}尾"
    return ""


def extract_open_number_tail(block: str, issue: str) -> str:
    marker = f"{issue}期"
    for line in block.splitlines():
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        match = re.search(r"开[:：](\d{1,2})", compact)
        if match:
            return f"{normalize_digits(match.group(1))[-1]}尾"
    return ""


def extract_next_line_bracket_tail(block: str, issue: str, keyword: str) -> str:
    lines = block.splitlines()
    marker = f"{issue}期"
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if marker not in compact or keyword not in compact:
            continue
        if index + 1 >= len(lines):
            return ""
        next_line = re.sub(r"\s+", "", lines[index + 1])
        match = re.fullmatch(r"[【\[]([0-9])[】\]]", next_line)
        if match:
            return f"{match.group(1)}尾"
        return ""
    return ""


def extract_strict_issue_tail(block: str, issue: str) -> str:
    marker = f"{issue}期"
    patterns = [
        r"平特一尾[】\]]*[【\[(（《〖{]([0-9]{1,4})尾?[】\])）》〗}]",
        r"精平一尾[^0-9]{0,10}[【\[(（《〖{]([0-9]{1,4})尾?[】\])）》〗}]",
        r"[【\[(（《〖{]([0-9]{1,4})尾[】\])）》〗}]",
        r"平特?([0-9])尾",
        r"平([0-9])尾",
    ]
    for line in block.splitlines():
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        if not re.search(r"平特|精平|平\d尾", compact):
            continue
        for pattern in patterns:
            match = re.search(pattern, compact)
            if match:
                digit = normalize_digit_tail(match.group(1))
                if digit:
                    return f"{digit}尾"
    return ""


def extract_site_dedicated_tail(
    block: str,
    issue: str,
    keyword: str,
    allow_formula: bool = True,
) -> str:
    marker = f"{issue}期"
    lines = block.splitlines()
    patterns = [
        r"平特(?:一|①|⑴|⒈|1)?尾«{1,2}([0-9]{1,4})尾?»{1,2}",
        r"平特([0-9]{1,4})尾",
        r"平尾[【\[(（]([0-9])[】\])）]",
        r"〔([0-9]{1,4})尾〕",
        r"平特[一①⑴⒈1]?尾[^0-9]{0,12}〈([0-9.\-]{1,8})〉",
        r"(?:平特|精平|稳中|杀)[^【\[《〖{＜<『┣╒〓]{0,14}尾[^0-9]{0,12}[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾?[】\])）》〗}＞>★╕┫〓]",
        r"[一①⑴⒈1]尾平特[^0-9]{0,12}[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾?[】\])）》〗}＞>★╕┫〓]",
        r"[【\[(（《〖{＜<★『┣╒〓][一①⑴⒈1]尾平特[】\])）》〗}＞>★╕┫〓][^0-9]{0,12}[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾?[】\])）》〗}＞>★╕┫〓]",
        r"二尾平特[^0-9]{0,12}[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾?[】\])）》〗}＞>★╕┫〓]尾?",
        r"[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾[】\])）》〗}＞>★╕┫〓]",
        r"平尾(\d)",
        r"平(?:特)?\s*(\d)尾",
        r"(?<![0-9])(\d)尾",
    ]
    if allow_formula:
        patterns.insert(0, r"[+＋]\d{1,2}\s*=\s*(\d)尾?")
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        signal_pattern = r"平特|精平|稳中|杀一尾|一尾平特|平尾\d|平尾|平\d尾|特尾|本期买"
        if allow_formula:
            signal_pattern += r"|\d尾|[+＋]\d{1,2}=\d"
        has_tail_signal = re.search(signal_pattern, compact)
        if has_tail_signal:
            for pattern in patterns:
                match = re.search(pattern, compact)
                if match:
                    digit = normalize_digit_tail(match.group(1))
                    if digit:
                        return f"{digit}尾"
        intermediate: list[str] = []
        keyword_key = normalize_search(keyword)
        for next_line in lines[index + 1 : index + 5]:
            next_compact = re.sub(r"\s+", "", next_line)
            if re.search(r"(?<![0-9])[0-9]+期", next_compact):
                break
            bracket = re.fullmatch(r"[【\[]([0-9]{1,4})尾?[】\]]", next_compact)
            if bracket:
                digit = normalize_digit_tail(bracket.group(1))
                if digit:
                    return f"{digit}尾"
                break
            if not next_compact:
                continue
            normalized_line = normalize_search(next_compact)
            is_target_repeat = bool(
                keyword_key
                and normalized_line
                and (
                    keyword_key in normalized_line
                    or normalized_line in keyword_key
                )
            )
            is_tail_semantic = bool(
                re.search(r"平特|平尾|特尾|一尾|二尾|两尾|精平|本期买", next_compact)
            )
            if is_tail_semantic:
                embedded = re.search(r"[【\[]([0-9]{1,4})尾?[】\]]", next_compact)
                if embedded:
                    digit = normalize_digit_tail(embedded.group(1))
                    if digit:
                        return f"{digit}尾"
            is_punctuation = not bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", next_compact))
            if not (is_target_repeat or is_tail_semantic or is_punctuation):
                break
            intermediate.append(next_compact)
    return ""


def extract_caimin_shuji_tail(block: str, issue: str) -> str:
    compact_issue = normalize_digits(issue)
    pattern = re.compile(rf"(?<![0-9]){re.escape(compact_issue)}期平0([0-9])尾(?![0-9])")
    for line in block.splitlines():
        compact = re.sub(r"\s+", "", line)
        match = pattern.search(compact)
        if match:
            return f"{match.group(1)}尾"
    return ""


def extract_sanxiancai(block: str, issue: str) -> str:
    marker = f"{issue}期"
    for line in block.splitlines():
        compact = re.sub(r"\s+", "", line)
        if marker not in compact:
            continue
        match = re.search(r"平特一尾[】\]]+[【\[](\d{1,4})尾[】\]]", compact)
        if match:
            digit = normalize_digit_tail(match.group(1))
            if digit:
                return f"{digit}尾"
    return ""


def extract_explicit_two_tail(window: str) -> str:
    compact = re.sub(r"\s+", "", window)
    if not re.search(r"平特(?:二|两)尾|(?:二|两)尾平特", compact):
        return ""
    pattern = (
        r"(?:平特(?:二|两)尾|(?:二|两)尾平特)[^0-9]{0,16}"
        r"[【\[(（《〈＜〔]([0-9](?:[.．、,，\-]?[0-9]))尾?[】\])）》〉＞〕]"
    )
    match = re.search(pattern, compact)
    if not match:
        return ""
    digits = re.findall(r"[0-9]", match.group(1))
    if len(digits) != 2:
        return ""
    if digits[0] == digits[1]:
        return f"{digits[0]}尾"
    return f"{digits[0]}尾 {digits[1]}尾"


def _numeric_two_tail_values(text: str) -> list[str]:
    values: list[str] = []
    brackets = (
        ("《", "》"),
        ("〈", "〉"),
        ("＜", "＞"),
        ("〖", "〗"),
        ("〔", "〕"),
        ("【", "】"),
        ("[", "]"),
        ("（", "）"),
        ("(", ")"),
    )
    for line in text.splitlines():
        compact = normalize_digits(re.sub(r"\s+", "", line))
        for marker in re.finditer(r"平特[2⒉]尾", compact):
            remainder = compact[marker.end() :]
            for opening, closing in brackets:
                start = remainder.find(opening)
                if start < 0:
                    continue
                end = remainder.find(closing, start + 1)
                if end < 0:
                    continue
                body = remainder[start + 1 : end]
                digits = re.findall(r"(?<![0-9])([0-9])尾", body)
                if len(digits) == 2:
                    values.append(f"{digits[0]}尾 {digits[1]}尾")
                break
    return values


def extract_numeric_two_tail(block: str, issue: str) -> str:
    marker = f"{normalize_digits(issue)}期"
    for line in block.splitlines():
        compact = normalize_digits(re.sub(r"\s+", "", line))
        if marker not in compact:
            continue
        values = _numeric_two_tail_values(compact)
        if values:
            return values[0]
    return ""


def tail_candidates(window: str, mode: str = "") -> list[str]:
    compact = re.sub(r"\s+", "", window)
    values: list[str] = []
    if mode == "next_line_bracket_tail":
        return values
    if mode == "numeric_two_tail":
        return _numeric_two_tail_values(window)
    if mode == "open_number_tail":
        for match in re.finditer(r"开[:：](\d{1,2})", compact):
            values.append(f"{normalize_digits(match.group(1))[-1]}尾")
        return values
    patterns = [
        r"[+＋]\d{1,2}\s*=\s*(\d)尾?",
        r"([0-9]{1,4})尾",
        r"平尾[【\[(（]?([0-9])[】\])）]?",
        r"平([0-9])尾",
        r"[【\[(（《〖{＜<★『┣╒〓]([0-9.\-]{1,8})尾?[】\])）》〗}＞>★╕┫〓]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            if pattern.startswith(r"[【"):
                preceding = compact[max(0, match.start() - 3) : match.start()]
                if re.search(r"开[:：]?$", preceding):
                    continue
                following = compact[match.end() : match.end() + 1]
                if following == "尾":
                    continue
            digit = normalize_digit_tail(match.group(1))
            if digit:
                values.append(f"{digit}尾")
    return values


def audit_flags(
    block: str,
    windows: list[str],
    matched_window: str,
    keyword: str,
    mode: str = "",
) -> tuple[str, ...]:
    flags: list[str] = []
    if not matched_window:
        flags.append("NO_RESULT")
    if keyword and not block:
        flags.append("KEYWORD_NOT_FOUND")
    if len(windows) > 1:
        flags.append("MULTIPLE_ISSUE_WINDOWS")
    candidates = tail_candidates(matched_window, mode)
    if len(set(candidates)) > 1:
        flags.append("MULTIPLE_TAIL_CANDIDATES")
    if re.search(r"开\s*[:：]\s*\d{1,2}", matched_window):
        flags.append("OPEN_NUMBER_NEARBY")
    matched_lines = matched_window.splitlines()
    if len(matched_lines) > 1 and any(re.fullmatch(r"\s*[【\[]?\d{1,4}尾?[】\]]?\s*", line) for line in matched_lines[1:]):
        flags.append("NEXT_LINE_VALUE")
    if len(normalize_search(keyword)) <= 4:
        flags.append("BROAD_KEYWORD")
    if block and not windows:
        flags.append("NO_ISSUE_WINDOW")
    return tuple(dict.fromkeys(flags))
