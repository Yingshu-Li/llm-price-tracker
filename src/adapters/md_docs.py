"""Tier 1a：厂商官方 markdown 文档价格表适配器。

五家厂商在文档 URL 后加 `.md` 会返回给 agent 读的 markdown。这比抓 HTML
稳定得多（没有 DOM、没有 class 名、没有 JS），而且是厂商自己维护的。

设计原则：**认不出的表格明确跳过并上报，绝不猜语义。**
少解析几张表只是覆盖率问题；猜错语义会产出看起来完全合理但实际是错的价格，
那才是真正危险的。所有跳过的表都会出现在 ParseReport.skipped 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..records import TIER_OFFICIAL, PriceRecord

# ── 单元格清洗 ──────────────────────────────────────────────────

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_BR = re.compile(r"<br\s*/?>", re.I)
# 数字里可能有千分位逗号，且 Zhipu 会把 $ 转义成 \$
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*\.?[0-9]*)")

# 表示"没有这项价格"的单元格。注意与 "Free"（价格确实是 0）区分开。
_MISSING_CELLS = {"", "-", "–", "—", "n/a", "na", "\\", "\\\\", "not available"}
_FREE_CELLS = {"free", "limited-time free", "limited time free", "free (limited-time)"}


def clean_cell(raw: str) -> str:
    """去掉 markdown 链接、<br />、转义反斜杠，保留可读文本。"""
    text = _MD_LINK.sub(r"\1", raw)
    text = _HTML_BR.sub(" ", text)
    text = text.replace("\\$", "$").replace("\\_", "_")
    return text.strip()


@dataclass
class Money:
    """一个价格单元格的解析结果，连同它的原始文本一起带走。"""

    value: float | None
    unit: str | None  # per_mtok | per_image | per_second | per_video | per_call | None
    raw: str
    is_free: bool = False


def parse_money(raw_cell: str) -> Money:
    """解析价格单元格。

    见过的形态：$5.00 / \\$1.4 / $10 / MTok / $0.02 / image / $0.050 / sec /
    Free / Limited-time Free / - / \\\\
    """
    text = clean_cell(raw_cell)
    low = text.lower().strip()

    if low in _MISSING_CELLS:
        return Money(None, None, raw_cell)
    if low in _FREE_CELLS:
        return Money(0.0, None, raw_cell, is_free=True)

    match = _MONEY.search(text)
    if not match:
        return Money(None, None, raw_cell)

    value = float(match.group(1).replace(",", ""))

    # 单位后缀决定这个数字是按什么计费的
    tail = text[match.end() :].lower()
    unit = None
    if re.search(r"/\s*(m\s*tok|mtok|1m\s*token|million\s*token)", tail):
        unit = "per_mtok"
    elif re.search(r"/\s*image", tail):
        unit = "per_image"
    elif re.search(r"/\s*(video|clip)", tail):
        unit = "per_video"
    elif re.search(r"/\s*(sec|second)", tail):
        unit = "per_second"
    elif re.search(r"/\s*(use|call|request)", tail):
        unit = "per_call"

    return Money(value, unit, raw_cell)


_CONTEXT = re.compile(r"([0-9][0-9,\.]*)\s*([kKmM])?\s*(?:tokens?)?")


def parse_context(raw_cell: str) -> int | None:
    """`500k` / `1M` / `1,048,576 tokens` → int"""
    text = clean_cell(raw_cell)
    if text.lower() in _MISSING_CELLS:
        return None
    match = _CONTEXT.match(text)
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


# 模型名里的尾部括号是价格分层限定（不是名字的一部分），要拆出来单独记
_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")


def split_qualifier(name: str) -> tuple[str, str | None]:
    """`gpt-5.5 (<272K context length)` → ("gpt-5.5", "<272K context length")

    同一个模型会因为分层限定产生多条观测，它们不能互相覆盖，
    所以限定条件必须保留而不能直接丢掉。
    """
    text = clean_cell(name)
    match = _TRAILING_PAREN.search(text)
    if not match:
        return text, None
    return text[: match.start()].strip(), match.group(1).strip()


# ── 列头 → 字段映射 ────────────────────────────────────────────
# 键是规范化后的列头，值是 (字段名, 列组)。列组用于 OpenAI 那种
# 短/长上下文并列的表：同一行会因此产出两条不同 qualifier 的记录。

_HEADER_MAP: dict[str, tuple[str, str | None]] = {
    # 通用
    "input": ("input_per_1m", None),
    "output": ("output_per_1m", None),
    "input tokens": ("input_per_1m", None),
    "output tokens": ("output_per_1m", None),
    "base input tokens": ("input_per_1m", None),
    "cached input": ("cache_read_per_1m", None),
    "cache hits & refreshes": ("cache_read_per_1m", None),
    "input price (cache hit)": ("cache_read_per_1m", None),
    "input price (cache miss)": ("input_per_1m", None),
    "input price": ("input_per_1m", None),
    "output price": ("output_per_1m", None),
    "5m cache writes": ("cache_write_per_1m", None),
    "1h cache writes": ("cache_write_1h_per_1m", None),
    # Anthropic 的批处理表（层级由章节标题 "Batch processing" 给出）
    "batch input": ("input_per_1m", None),
    "batch output": ("output_per_1m", None),
    # OpenAI 视频表的按秒计价列
    "price per second": ("per_second", None),
    # OpenAI 短/长上下文双列组
    "short context input": ("input_per_1m", "short"),
    "short context cached input": ("cache_read_per_1m", "short"),
    "short context cache writes": ("cache_write_per_1m", "short"),
    "short context output": ("output_per_1m", "short"),
    "long context input": ("input_per_1m", "long"),
    "long context cached input": ("cache_read_per_1m", "long"),
    "long context cache writes": ("cache_write_per_1m", "long"),
    "long context output": ("output_per_1m", "long"),
    # 非价格的维度列
    "model": ("__model__", None),
    "context": ("__context__", None),
    "context window": ("__context__", None),
    "modality": ("__modality__", None),
    "unit": ("__unit__", None),
}

# 单价表（只有一个价格列）的列头，实际字段由章节的单位提示决定
_SINGLE_PRICE_HEADERS = {"price", "cost"}

# 可以作为"名字列"的列头。模型名不一定在第 0 列。
_NAME_HEADERS = {"model", "mode", "tool", "agent"}

# 章节里的单位提示，决定单价表的价格算到哪个字段上
_SECTION_UNIT_HINTS = (
    (re.compile(r"per\s+1m\s+tokens", re.I), "per_mtok"),
    (re.compile(r"per\s+image", re.I), "per_image"),
    (re.compile(r"per\s+video", re.I), "per_video"),
    (re.compile(r"per\s+second|per\s+sec\b", re.I), "per_second"),
)

_UNIT_TO_FIELD = {
    "per_image": "per_image",
    "per_video": "per_video",
    "per_second": "per_second",
    "per_call": "per_call",
}

# 服务层级：OpenAI 把 Standard/Batch/Flex/Fast 拆成并列的表
_TIER_PATTERNS = (
    (re.compile(r"\bbatch\b", re.I), "batch"),
    (re.compile(r"\bflex\b", re.I), "flex"),
    (re.compile(r"\bfast\b", re.I), "fast"),
    (re.compile(r"\bpriority\b", re.I), "priority"),
    (re.compile(r"\bstandard\b", re.I), "standard"),
)


# 列头里的单位后缀先剥掉再查表，否则 xAI 的 "input / 1m tokens" 和
# Anthropic 的 "input" 会被当成两个不同的列头。
_HEADER_UNIT_SUFFIX = re.compile(
    r"\s*/\s*(1m\s*tokens?|mtok|million\s*tokens?|token|image|video)\s*$", re.I
)


def normalize_header(cell: str) -> str:
    text = clean_cell(cell).lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = _HEADER_UNIT_SUFFIX.sub("", text).strip()
    return text


@dataclass
class SkippedTable:
    """没能识别的表格。记下来，让"没解析"是可见的而不是静默丢失。"""

    section: str
    header: list[str]
    reason: str
    line_no: int


@dataclass
class UnmappedColumn:
    """认得表、也解析出了记录，但某一列的列头不认识。

    这种"部分列静默丢失"比整表跳过更危险：表格照样产出记录，看起来一切正常，
    实际上少了一个价格维度（真实案例：Moonshot 的 `Input Price` 没进映射表，
    结果那批模型只有输出价没有输入价）。所以单独报出来。
    """

    section: str
    column: str
    line_no: int


@dataclass
class ParseReport:
    records: list[PriceRecord] = field(default_factory=list)
    skipped: list[SkippedTable] = field(default_factory=list)
    unmapped: list[UnmappedColumn] = field(default_factory=list)
    tables_seen: int = 0
    tables_parsed: int = 0


# ── 表格切分 ────────────────────────────────────────────────────

_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$")


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def parse_markdown_tables(text: str):
    """产出 (章节标题, 章节上下文文本, 列头, 数据行, 行号)。"""
    lines = text.splitlines()
    heading = ""
    # 章节上下文：标题之后、表格之前的散文，单位提示藏在这里
    context_buffer: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            context_buffer = []
            i += 1
            continue

        if (
            _is_table_row(line)
            and i + 1 < len(lines)
            and _SEPARATOR_ROW.match(lines[i + 1])
        ):
            header = [normalize_header(c) for c in _split_row(line)]
            header_line_no = i + 1
            i += 2
            rows: list[tuple[list[str], int]] = []
            while i < len(lines) and _is_table_row(lines[i]):
                rows.append((_split_row(lines[i]), i + 1))
                i += 1
            yield heading, "\n".join(context_buffer), header, rows, header_line_no
            context_buffer = []
            continue

        if line.strip():
            context_buffer.append(line.strip())
            context_buffer = context_buffer[-6:]
        i += 1


def _section_unit(heading: str, context: str) -> str | None:
    blob = f"{heading}\n{context}"
    for pattern, unit in _SECTION_UNIT_HINTS:
        if pattern.search(blob):
            return unit
    return None


def _service_tier(heading: str) -> str:
    for pattern, tier in _TIER_PATTERNS:
        if pattern.search(heading):
            return tier
    return "standard"


def parse_pricing_doc(
    text: str,
    *,
    source: str,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
    provider: str,
    default_unit: str = "per_mtok",
) -> ParseReport:
    """把一份官方 markdown 定价文档解析成 PriceRecord 列表。"""
    report = ParseReport()

    for heading, context, header, rows, line_no in parse_markdown_tables(text):
        report.tables_seen += 1

        # 模型名不一定在第 0 列：OpenAI 有 `| Category | Model | Input | ... |`
        name_idx = next(
            (i for i, c in enumerate(header) if c in _NAME_HEADERS), None
        )
        if name_idx is None:
            report.skipped.append(
                SkippedTable(heading, header, "找不到模型/工具名列", line_no)
            )
            continue

        mapping: dict[int, tuple[str, str | None]] = {}
        single_price_cols: list[int] = []
        for idx, col in enumerate(header):
            if idx == name_idx:
                continue
            if col in _HEADER_MAP:
                mapping[idx] = _HEADER_MAP[col]
            elif col in _SINGLE_PRICE_HEADERS:
                single_price_cols.append(idx)
            else:
                report.unmapped.append(UnmappedColumn(heading, col, line_no))

        section_unit = _section_unit(heading, context)

        if not mapping and not single_price_cols:
            report.skipped.append(
                SkippedTable(heading, header, "没有可识别的价格列", line_no)
            )
            continue

        # 单价表的单位可能来自章节提示（"Prices per image."），也可能就写在
        # 单元格里（"$0.02 / image"）。两者都没有的行会在下面逐行跳过，
        # 整表颗粒无收时由末尾的零产出检查兜底上报。
        tier = _service_tier(heading)
        parsed_any = False

        for cells, row_line_no in rows:
            if len(cells) <= name_idx:
                continue
            model_name, qualifier = split_qualifier(cells[name_idx])
            if not model_name:
                continue

            snippet = "| " + " | ".join(cells) + " |"

            # 按列组分桶：OpenAI 短/长上下文会各产出一条记录
            groups: dict[str | None, dict[str, float]] = {}
            context_length: int | None = None
            modality: str | None = None
            row_unit: str | None = None

            for idx, (field_name, group) in mapping.items():
                if idx >= len(cells):
                    continue
                cell = cells[idx]
                if field_name == "__context__":
                    context_length = parse_context(cell)
                    continue
                if field_name == "__modality__":
                    modality = clean_cell(cell) or None
                    continue
                if field_name == "__unit__":
                    row_unit = clean_cell(cell) or None
                    continue
                money = parse_money(cell)
                if money.value is None:
                    continue
                groups.setdefault(group, {})[field_name] = money.value

            for idx in single_price_cols:
                if idx >= len(cells):
                    continue
                money = parse_money(cells[idx])
                if money.value is None:
                    continue
                unit = money.unit or section_unit
                target = _UNIT_TO_FIELD.get(unit or "", None)
                if target is None and unit == "per_mtok":
                    # 单价表按 token 计费时，无法区分输入/输出，记为输入并在
                    # qualifier 里标明，避免伪造一个不存在的输出价
                    target = "input_per_1m"
                if target is None:
                    continue
                groups.setdefault(None, {})[target] = money.value

            for group, prices in groups.items():
                if not prices:
                    continue
                group_qualifier = qualifier
                if group == "long":
                    group_qualifier = (
                        f"{qualifier}; long context" if qualifier else "long context"
                    )
                elif group == "short" and qualifier is None:
                    group_qualifier = None

                unit_original = row_unit or (
                    "per 1M tokens" if section_unit in (None, "per_mtok") else
                    section_unit.replace("_", " ")
                )

                try:
                    report.records.append(
                        PriceRecord(
                            source=source,
                            source_tier=TIER_OFFICIAL,
                            is_official=True,
                            source_url=source_url,
                            fetched_at=fetched_at,
                            source_snippet=snippet,
                            unit_original=unit_original,
                            source_version=source_version,
                            model_id=model_name,
                            provider=provider,
                            qualifier=group_qualifier,
                            service_tier=tier,
                            context_length=context_length,
                            modality=modality,
                            raw={"section": heading, "line": row_line_no},
                            **prices,
                        )
                    )
                    parsed_any = True
                except ValueError:
                    # 构造期断言失败（比如整行没有任何价格）——跳过该行而不是
                    # 让整个源挂掉，但计入 skipped 以便发现
                    report.skipped.append(
                        SkippedTable(
                            heading, header, f"第 {row_line_no} 行构造失败", row_line_no
                        )
                    )

        if parsed_any:
            report.tables_parsed += 1
        else:
            # 列头能映射却一条都没产出，通常是列头别名没覆盖到。
            # 不报出来的话这种失败是完全静默的——比识别不出表头危险得多。
            report.skipped.append(
                SkippedTable(
                    heading, header, "列头可映射但未产出任何记录", line_no
                )
            )

    return report


# ── Moonshot 的 MDX DocTable ────────────────────────────────────
# Moonshot 不用管道表，用的是 JSX 组件，价格还包在 <>{"$"}0.30</> 里。

_DOCTABLE = re.compile(
    r"<DocTable\s+columns=\{\[(?P<cols>.*?)\]\}\s*rows=\{\[(?P<rows>.*?)\]\}\s*/>",
    re.S,
)
_COL_TITLE = re.compile(r'title:\s*"([^"]*)"')
_JSX_FRAGMENT = re.compile(r'<>\s*\{"([^"]*)"\}\s*([0-9][0-9,\.]*)\s*</>')


def _clean_doctable_cell(cell: str) -> str:
    cell = cell.strip().rstrip(",").strip()
    cell = _JSX_FRAGMENT.sub(r"\1\2", cell)  # <>{"$"}0.30</> → $0.30
    if cell.startswith('"') and cell.endswith('"'):
        cell = cell[1:-1]
    return cell.strip()


def _split_doctable_row(row_src: str) -> list[str]:
    """按顶层逗号切分一行，跳过引号内和 <> </> 内的逗号。"""
    cells, depth, in_quote, current = [], 0, False, []
    for ch in row_src:
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch == "<":
            depth += 1
        elif not in_quote and ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0 and not in_quote:
            cells.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        cells.append("".join(current))
    return [_clean_doctable_cell(c) for c in cells]


def parse_doctable_doc(
    text: str,
    *,
    source: str,
    source_url: str,
    fetched_at: str,
    source_version: str | None,
    provider: str,
) -> ParseReport:
    """解析 Moonshot 的 <DocTable columns={...} rows={...} /> 组件。"""
    report = ParseReport()

    for match in _DOCTABLE.finditer(text):
        report.tables_seen += 1
        header = [normalize_header(t) for t in _COL_TITLE.findall(match.group("cols"))]
        if not header or header[0] != "model":
            report.skipped.append(SkippedTable("DocTable", header, "首列不是 model", 0))
            continue

        mapping = {}
        for idx, col in enumerate(header):
            if idx == 0:
                continue
            if col in _HEADER_MAP:
                mapping[idx] = _HEADER_MAP[col]
            else:
                report.unmapped.append(UnmappedColumn("DocTable", col, 0))
        if not mapping:
            report.skipped.append(
                SkippedTable("DocTable", header, "没有可识别的价格列", 0)
            )
            continue

        rows_src = match.group("rows")
        parsed_any = False
        for row_match in re.finditer(r"\[(.*?)\]\s*,?\s*(?=\[|$)", rows_src, re.S):
            cells = _split_doctable_row(row_match.group(1))
            if len(cells) < 2:
                continue
            model_name, qualifier = split_qualifier(cells[0])
            if not model_name:
                continue

            snippet = "[" + ", ".join(cells) + "]"
            prices: dict[str, float] = {}
            context_length: int | None = None
            unit_original = "per 1M tokens"

            for idx, (field_name, _group) in mapping.items():
                if idx >= len(cells):
                    continue
                if field_name == "__context__":
                    context_length = parse_context(cells[idx])
                    continue
                if field_name == "__unit__":
                    unit_original = cells[idx] or unit_original
                    continue
                money = parse_money(cells[idx])
                if money.value is not None:
                    prices[field_name] = money.value

            if not prices:
                continue

            try:
                report.records.append(
                    PriceRecord(
                        source=source,
                        source_tier=TIER_OFFICIAL,
                        is_official=True,
                        source_url=source_url,
                        fetched_at=fetched_at,
                        source_snippet=snippet,
                        unit_original=unit_original,
                        source_version=source_version,
                        model_id=model_name,
                        provider=provider,
                        qualifier=qualifier,
                        context_length=context_length,
                        raw={"format": "mdx_doctable"},
                        **prices,
                    )
                )
                parsed_any = True
            except ValueError:
                report.skipped.append(
                    SkippedTable("DocTable", header, "构造失败", 0)
                )

        if parsed_any:
            report.tables_parsed += 1

    return report
