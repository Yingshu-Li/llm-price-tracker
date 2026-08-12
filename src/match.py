"""把 raw.csv 的模型名匹配到各源的模型 id。

分级策略，越靠前越可信：
  0 alias     人工在 aliases.yaml 里确认过的，最高优先级，永久生效
  1 exact     规范化后完全相同
  2 contains  一方比另一方多一截**日期快照**后缀（`gpt-5` vs `gpt-5-20260801`）

不做模糊匹配。相似度高不等于同一个模型——`GPT-5.6 Sol Pro` 与 `gpt-5.6-sol`
相似度 0.85，却是两个不同的模型。匹配不上就是匹配不上，由导出层标注为 not_found。

匹配**限制在同一家公司内**。这不只是精度优化，更是结构性防线：
raw.csv 里 iFLYTEK 的 `Spark Pro` 和 OpenAI 的 `gpt-5.3-codex-spark` 在
任何跨公司模糊匹配下都可能撞上，限制公司后这类误配不可能发生。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

from .normalize import RawModel, name_candidates, norm

METHOD_ALIAS = "alias"
METHOD_EXACT = "exact"
METHOD_CONTAINS = "contains"

# 各方法的置信度，写入 Match 供下游判断。
_CONFIDENCE = {
    METHOD_ALIAS: 1.0,
    METHOD_EXACT: 0.95,
    METHOD_CONTAINS: 0.80,
}



# 出现在后缀里就说明这是**另一个模型**而不是同一模型的快照版本。
# 漏一个词的代价是把 A 的价格安到 B 头上，所以这里宁可列全。
_VARIANT_TOKENS = {
    "pro", "mini", "nano", "micro", "small", "medium", "large", "xl",
    "turbo", "flash", "flashx", "lite", "air", "airx", "plus", "max", "ultra",
    "thinking", "instant", "reasoning", "nonreasoning", "chat", "code", "coder",
    "vision", "audio", "image", "video", "embedding", "rerank", "guard",
    "highspeed", "fast", "batch", "agent", "search", "ocr", "asr", "tts",
}


def _suffix_is_snapshot(a: str, b: str) -> bool:
    """a、b 中较长的一方是否只是较短一方加了日期/快照后缀。

    允许：`claude-opus-4-5` ↔ `claude-opus-4-5-20251101`（日期）
          `grok-4-5` ↔ `grok-4-5-latest`
    拒绝：`gpt-5-6-sol` ↔ `gpt-5-6-sol-pro`（pro 是另一个模型）
    """
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if not longer.startswith(shorter + "-"):
        return False

    tokens = longer[len(shorter) + 1 :].split("-")
    if not all(tokens):
        return False
    if any(t in _VARIANT_TOKENS for t in tokens):
        return False

    floating = {"latest", "preview", "stable"}
    if all(t in floating for t in tokens):
        return True

    if not all(t.isdigit() for t in tokens):
        return False

    # ⚠️ 纯数字后缀既可能是日期快照，也可能是**版本号**，两者意义完全相反：
    #   `claude-opus-4-5` + `20251101`  → 同一模型的日期快照，应匹配
    #   `minimax-m2`      + `7`         → M2.7 是**另一个版本**，不能匹配
    # 用总位数区分：日期至少 4 位（0309 / 202608 / 20251101），
    # 版本号分量通常 1–2 位。真实事故：MiniMax-M2.7 曾被匹配到 Minimax M2。
    return len("".join(tokens)) >= 4


@dataclass
class Match:
    raw_model: str
    source: str
    source_model_id: str
    method: str
    confidence: float



@dataclass
class MatchReport:
    matches: list[Match]
    method_counts: dict[str, int]
    matched_models: set[str]


def load_aliases(path: str | Path) -> dict[str, dict[str, str]]:
    """aliases.yaml: {raw_model: {source: source_model_id}}

    文件不存在是正常状态（第一次跑还没有人工确认过任何东西）。
    """
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("aliases", {}) or {}


def _index_by_company(records) -> dict[str, dict[str, list]]:
    """{company: {规范化候选形式: [records]}}

    ⚠️ 源侧必须走**和 raw 侧同一个** name_candidates()。曾经只对 raw 侧剥
    公司前缀，导致 `NVIDIA Nemotron 3 Super` 与 `Nemotron 3 Super` 永远对不上，
    NVIDIA / Amazon 整批 0 匹配。规范化不对称是这类系统最隐蔽的失效模式。
    """
    index: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        company = record.raw.get("company")
        if not company:
            continue
        for key in name_candidates(record.model_id, company):
            index[company][key].append(record)
    return index


def match_all(
    raw_models: list[RawModel],
    records: list,
    aliases: dict[str, dict[str, str]],
) -> MatchReport:
    """把 raw.csv 的每一行匹配到 0..N 个源模型 id。"""
    index = _index_by_company(records)
    matches: list[Match] = []
    method_counts: dict[str, int] = defaultdict(int)
    matched_models: set[str] = set()

    for raw in raw_models:
        company_index = index.get(raw.company, {})
        if not company_index:
            # 该公司本轮没有任何源覆盖——这是覆盖缺口，不是匹配失败。
            # 导出层会把它标成 not_found。
            continue

        found = False

        # 0) 人工别名优先，且直接采信
        for source, source_model_id in (aliases.get(raw.model) or {}).items():
            if any(
                r.model_id == source_model_id
                for bucket in company_index.values()
                for r in bucket
            ):
                matches.append(
                    Match(
                        raw.model,
                        source,
                        source_model_id,
                        METHOD_ALIAS,
                        _CONFIDENCE[METHOD_ALIAS],
                    )
                )
                method_counts[METHOD_ALIAS] += 1
                found = True
        if found:
            matched_models.add(raw.model)
            continue

        raw_candidates = raw.candidates
        # 一个源模型只登记一次。同一个 model_id 在源里往往有几十条观测
        # （standard/batch/flex/fast × 短/长上下文），逐条登记会让下游按
        # (source, model_id) 展开时产生平方级重复。
        seen_targets: set[tuple[str, str]] = set()

        def _record_match(record, method: str) -> None:
            key = (record.source, record.model_id)
            if key in seen_targets:
                return
            seen_targets.add(key)
            matches.append(
                Match(raw.model, record.source, record.model_id, method, _CONFIDENCE[method])
            )

        # 1) 规范化后完全相同。
        # ⚠️ 不能在第一个命中的候选形式上 break：同一个 raw 模型的不同候选形式
        # 会命中不同的源（`claude-opus-4-5-20251101` 命中 models.dev，
        # `claude-opus-4-5` 才命中 Anthropic 官方文档）。提前 break 会让
        # 低优先级的源挡住高优先级的官方牌价——Anthropic 曾因此从 10 掉到 7。
        # 全部收集，优先级留给导出层的 pick_best 决定。
        for candidate in raw_candidates:
            for record in company_index.get(candidate, []):
                _record_match(record, METHOD_EXACT)
                found = True

        # 2) 一方比另一方多一截后缀。只有当多出来的部分是**日期/快照版本**时
        #    才认为是同一个模型；多出来的是语义变体（pro/mini/flash…）时必须
        #    拒绝——真实事故：`GPT-5.6 Sol Pro` 曾被匹配到 `gpt-5.6-sol`，
        #    而源里根本没有 sol-pro，等于凭空给它安了一个别的模型的价格。
        if not found:
            for candidate in raw_candidates:
                for key, bucket in company_index.items():
                    if key == candidate:
                        continue
                    if not _suffix_is_snapshot(candidate, key):
                        continue
                    for record in bucket:
                        _record_match(record, METHOD_CONTAINS)
                        found = True
                if found:
                    break

        if found:
            matched_models.add(raw.model)
            continue


    # method_counts 用实际写入的 matches 重算，避免上面分支里累加出错
    method_counts = defaultdict(int)
    for m in matches:
        method_counts[m.method] += 1

    return MatchReport(
        matches=matches,
        method_counts=dict(method_counts),
        matched_models=matched_models,
    )
