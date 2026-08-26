"""模型输入模态目录。

输入能力与价格是两个正交维度：没有价格的开源模型仍有明确的输入模态，
同一个价格源里的 ``modality`` 又可能表示计费通道（例如 audio token），不能
复用。这里从 vendored 的模型规格目录和人工核验表构造独立记录，再沿用现有的
同公司、精确名称匹配机制关联到 raw.csv。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .normalize import RawModel, infer_company


CANONICAL_MODALITIES = ("text", "image", "audio", "video", "pdf")

_PROVIDER_COMPANIES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "xai": "xAI",
    "mistral": "Mistral AI",
    "mistralai": "Mistral AI",
    "deepseek": "DeepSeek",
    "qwen": "Alibaba / Qwen",
    "cohere": "Cohere",
    "minimax": "MiniMax",
    "moonshotai": "Moonshot AI / Kimi",
}


@dataclass(frozen=True)
class ModalityRecord:
    source: str
    model_id: str
    input_modalities: tuple[str, ...]
    source_url: str
    priority: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class InputCapability:
    input_modalities: tuple[str, ...]
    sources: tuple[str, ...]
    source_urls: tuple[str, ...]

    @property
    def modalities_cell(self) -> str:
        return " | ".join(self.input_modalities)

    @property
    def sources_cell(self) -> str:
        return " | ".join(self.sources)

    @property
    def source_urls_cell(self) -> str:
        return " | ".join(self.source_urls)


def _canonical(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    normalized = {str(value).strip().lower() for value in (values or [])}
    aliases = {"document": "pdf", "documents": "pdf", "vision": "image"}
    normalized = {aliases.get(value, value) for value in normalized}
    return tuple(value for value in CANONICAL_MODALITIES if value in normalized)


def parse_modelsdev_modalities(payload: dict) -> list[ModalityRecord]:
    records: list[ModalityRecord] = []
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            modalities = _canonical((model.get("modalities") or {}).get("input"))
            if not modalities:
                continue
            company = infer_company(model_id) or infer_company(model.get("name") or "")
            if not company:
                continue
            provider_company = (
                infer_company(provider_id)
                or _PROVIDER_COMPANIES.get(str(provider_id).lower())
            )
            first_party = provider_company == company
            records.append(
                ModalityRecord(
                    source="modelsdev",
                    model_id=model_id,
                    input_modalities=modalities,
                    source_url="https://models.dev",
                    # 原厂目录优先于第三方托管目录；第三方可能额外提供 PDF
                    # 解析封装，那不应覆盖模型本身的原生输入能力。
                    priority=10 if first_party else 30,
                    raw={
                        "company": company,
                        "provider": provider_id,
                        "first_party": first_party,
                    },
                )
            )
    return records


def _litellm_modalities(entry: dict) -> tuple[str, ...]:
    values = set(entry.get("supported_modalities") or [])
    if entry.get("supports_vision") or entry.get("supports_image_input"):
        values.add("image")
    if entry.get("supports_embedding_image_input"):
        values.add("image")
    if entry.get("supports_audio_input"):
        values.add("audio")
    if entry.get("supports_video_input"):
        values.add("video")

    mode = str(entry.get("mode") or "").lower()
    # 这三个成品表中的通用、编码和向量模型均以文本为基础输入；显式列出的
    # 多模态能力在此基础上叠加。图像生成/语音转写等其他 Function 不在本次
    # 导出范围，因此不会被这个回退规则误标。
    if mode in {
        "chat", "completion", "responses", "embedding", "rerank",
        "text_completion", "search",
    }:
        values.add("text")
    return _canonical(values)


def parse_litellm_modalities(payload: dict) -> list[ModalityRecord]:
    records: list[ModalityRecord] = []
    for key, entry in payload.items():
        if key == "sample_spec" or not isinstance(entry, dict):
            continue
        modalities = _litellm_modalities(entry)
        if not modalities:
            continue
        model_id = key.split("/")[-1]
        seller = entry.get("litellm_provider") or (
            key.split("/")[0] if "/" in key else ""
        )
        company = (
            infer_company(key)
            or infer_company(model_id)
            or infer_company(str(seller))
            or ("Cohere" if model_id.lower().startswith("cohere.") else None)
        )
        if not company:
            continue
        records.append(
            ModalityRecord(
                source="litellm",
                model_id=model_id,
                input_modalities=modalities,
                source_url="https://github.com/BerriAI/litellm",
                priority=20,
                raw={"company": company, "provider": seller},
            )
        )
    return records


def load_manual_modalities(
    path: str | Path, raw_models: list[RawModel] | None = None
) -> list[ModalityRecord]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    records: list[ModalityRecord] = []
    defaults = data.get("company_defaults", {}) or {}
    for raw_model in raw_models or []:
        # 图像生成模型不能套用公司 LLM 家族的“默认文本输入”：同一家公司的
        # generation / edit / 3D 型号可能分别支持 text、image 或两者。它们必须
        # 由结构化模型目录或下方 models 的型号级官方证据明确给出。
        if raw_model.function == "Image Generation":
            continue
        item = defaults.get(raw_model.company)
        if not item:
            continue
        modalities = _canonical(item.get("input_modalities"))
        if not modalities:
            raise ValueError(f"公司默认输入模态为空：{raw_model.company!r}")
        records.append(
            ModalityRecord(
                source="official_family_catalog",
                model_id=raw_model.model,
                input_modalities=modalities,
                source_url=str(item["source_url"]),
                # 只用于结构化目录未覆盖的型号；任何原厂/聚合规格都优先。
                priority=50,
                raw={"company": raw_model.company, "note": item.get("note", "")},
            )
        )
    for item in data.get("models", []):
        modalities = _canonical(item.get("input_modalities"))
        if not modalities:
            raise ValueError(f"输入模态为空：{item.get('model_id')!r}")
        records.append(
            ModalityRecord(
                source="official_model_doc",
                model_id=str(item["model_id"]),
                input_modalities=modalities,
                source_url=str(item["source_url"]),
                priority=0,
                raw={"company": str(item["company"]), "note": item.get("note", "")},
            )
        )
    return records


def select_capability(records: list[ModalityRecord]) -> InputCapability | None:
    """选择最高可信层，并在同层记录之间取并集。

    同层并集表示该模型由同等可信的多个端点确认支持某模态；低可信的托管商
    记录不会给原厂记录额外加上 PDF/视觉等平台封装能力。
    """
    if not records:
        return None
    best_priority = min(record.priority for record in records)
    chosen = [record for record in records if record.priority == best_priority]
    modalities = _canonical(
        value for record in chosen for value in record.input_modalities
    )
    return InputCapability(
        input_modalities=modalities,
        sources=tuple(sorted({record.source for record in chosen})),
        source_urls=tuple(sorted({record.source_url for record in chosen})),
    )
