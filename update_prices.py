#!/usr/bin/env python3
"""抓取全部价格源，匹配到 raw.csv，导出一张带完整溯源的总表。

    python update_prices.py              # 抓取并导出
    python update_prices.py --dry-run    # 只打印统计，不写文件
    python update_prices.py --refresh-vendor   # 同时刷新本地 vendor/ 副本

raw.csv 是只读的人工清单，本脚本永远不会修改它。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from src import export as export_mod
from src.adapters import (
    ai302,
    aws_bedrock,
    azure_retail,
    china_official,
    iflytek,
    image_official,
    nanogpt,
    nonusd_official,
    price_apis,
    sensenova,
    upstage,
    vendored,
)
from src.adapters.md_docs import parse_doctable_doc, parse_pricing_doc
from src.fx import convert_records_to_usd, load_ecb_rates
from src.http import fetch
from src.match import load_aliases, match_all
from src.modalities import (
    load_manual_modalities,
    parse_litellm_modalities,
    parse_modelsdev_modalities,
    select_capability,
)
from src.normalize import infer_company, load_raw

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
OUT = ROOT / "out"

AZURE_MAX_PAGES = 12


def _fetch_entry(source, url, ok, result, **extra):
    return {
        "source": source,
        "url": url,
        "ok": ok,
        "status": result.status if result else None,
        "version": result.version if result else None,
        "fetched_at": result.fetched_at if result else "",
        "from_cache": result.from_cache if result else False,
        "error": result.error if result else None,
        "error_kind": result.error_kind if result else None,
        "n_records": 0,
        **extra,
    }


def collect_official_md() -> tuple[list, list[dict], list[str]]:
    """Tier 1a：厂商官方 markdown 定价文档。"""
    cfg = yaml.safe_load((CONFIG / "official_sources.yaml").read_text("utf-8"))
    records, fetches, warnings = [], [], []
    for spec in cfg["sources"]:
        for url in spec["urls"]:
            r = fetch(url, use_cache=True)
            entry = _fetch_entry(
                spec["id"], r.url, r.ok, r,
                provider_name=f"{spec['company']} 官方文档",
                weblink=spec["urls"][0].removesuffix(".md"),
                license="厂商官方文档",
            )
            fetches.append(entry)
            if not r.ok:
                print(f"  ✗ {spec['id']:16} {r.error}", file=sys.stderr)
                continue
            parser = (
                parse_doctable_doc
                if spec.get("format") == "mdx_doctable"
                else parse_pricing_doc
            )
            rep = parser(
                r.text, source=spec["id"], source_url=r.url,
                fetched_at=r.fetched_at, source_version=r.version,
                provider=spec["provider"],
            )
            for rec in rep.records:
                rec.raw["company"] = spec["company"]
                rec.raw["provider_name"] = f"{spec['company']} 官方文档"
                rec.raw["weblink"] = entry["weblink"]
            records += rep.records
            entry["n_records"] = len(rep.records)
            # 认不出的列头单独报出来：表格照样产出记录，但少了一个价格维度，
            # 表面完全正常。真实事故见 export.write_sources_md 的注释。
            for col in {u.column for u in rep.unmapped}:
                warnings.append(f"{spec['id']}: 未识别的列头 `{col}`")
            for sk in rep.skipped:
                warnings.append(f"{spec['id']}: 跳过表格「{sk.section}」— {sk.reason}")
    print(f"  ✓ 厂商官方文档   {sum(1 for f in fetches if f['ok']):2}/{len(fetches)} 个页面，"
          f"{sum(f['n_records'] for f in fetches):4} 条，{len(warnings)} 条解析告警")
    return records, fetches, warnings


def collect_aws() -> tuple[list, list[dict]]:
    records, fetches = [], []
    for offer in aws_bedrock.OFFERS:
        url = aws_bedrock.offer_csv_url(offer)
        r = fetch(url, use_cache=True)
        entry = _fetch_entry(
            f"aws_{offer}", r.url, r.ok, r,
            provider_name="AWS Bedrock",
            weblink="https://aws.amazon.com/bedrock/pricing/",
            license="AWS 公开价格表",
        )
        fetches.append(entry)
        if not r.ok:
            print(f"  ✗ aws_{offer:24} {r.error}", file=sys.stderr)
            continue
        parsed = aws_bedrock.parse_offer(
            r.text, offer=offer, source_url=r.url,
            fetched_at=r.fetched_at, source_version=r.version,
        )
        for rec in parsed:
            rec.raw["provider_name"] = "AWS Bedrock"
            rec.raw["weblink"] = entry["weblink"]
        records += parsed
        entry["n_records"] = len(parsed)
    print(f"  ✓ AWS Bedrock    {sum(f['n_records'] for f in fetches):4} 条")
    return records, fetches


def collect_nonusd_official() -> tuple[list, list[dict], list[str]]:
    """Tier 1b：官网 HTML 中以本币计价的价格。"""
    r = fetch(nonusd_official.BAICHUAN_URL, use_cache=True)
    entry = _fetch_entry(
        "baichuan_official_html", r.url, r.ok, r,
        provider_name="Baichuan AI 官方价格",
        weblink=nonusd_official.BAICHUAN_URL,
        license="厂商官方价格页",
    )
    if not r.ok:
        print(f"  ✗ baichuan_official_html {r.error}", file=sys.stderr)
        return [], [entry], []
    records, warnings = nonusd_official.parse_baichuan(
        r.text, source_url=r.url, fetched_at=r.fetched_at,
        source_version=r.version,
    )
    for rec in records:
        rec.raw["company"] = "Baichuan AI"
        rec.raw["provider_name"] = "Baichuan AI 官方价格"
        rec.raw["weblink"] = nonusd_official.BAICHUAN_URL
    entry["n_records"] = len(records)
    print(f"  ✓ 百川人民币官网价 {len(records):4} 条")
    return records, [entry], warnings


def collect_image_official() -> tuple[list, list[dict], list[str]]:
    """Tier 1：图像生成厂商的官方**按张**价。

    与 collect_china_official 同构，只是这两家报的是美元按张价而非人民币
    token 价，口径不同，所以单独成一个 collector。
    """
    records, fetches, warnings = [], [], []
    specs = [
        (
            "bfl_official", "Black Forest Labs", image_official.BFL_URL,
            image_official.parse_bfl, "BFL 官方按张价",
        ),
        (
            "bria_official", "Bria", image_official.BRIA_URL,
            image_official.parse_bria, "Bria 官方按张价",
        ),
    ]
    for source_id, company, url, parser, pretty in specs:
        result = fetch(url, use_cache=True, timeout=60)
        entry = _fetch_entry(
            source_id, result.url, result.ok, result,
            provider_name=pretty, weblink=url, license="厂商官方价格页",
        )
        fetches.append(entry)
        if not result.ok:
            print(f"  ✗ {source_id:32} {result.error}", file=sys.stderr)
            continue
        parsed, parsed_warnings = parser(
            result.text, source_url=result.url, fetched_at=result.fetched_at,
            source_version=result.version,
        )
        for rec in parsed:
            rec.raw["company"] = company
            rec.raw["provider_name"] = pretty
        records += parsed
        warnings += parsed_warnings
        entry["n_records"] = len(parsed)
        print(f"  ✓ {pretty:22} {len(parsed):4} 条")
    return records, fetches, warnings


def collect_china_official() -> tuple[list, list[dict], list[str]]:
    """Tier 1：中国厂商当前 HTML 价目表与可审计人工快照。"""
    records, fetches, warnings = [], [], []
    specs = [
        (
            "stepfun_official_html", "StepFun", china_official.STEPFUN_URL,
            china_official.parse_stepfun, "StepFun 官方价格",
        ),
        (
            "baidu_qianfan_official_html", "Baidu / ERNIE", china_official.BAIDU_URL,
            china_official.parse_baidu, "百度千帆官方价格",
        ),
        (
            "tencent_tokenhub_official_html", "Tencent / Hunyuan", china_official.TENCENT_URL,
            china_official.parse_tencent, "腾讯 TokenHub 官方价格",
        ),
    ]
    for source_id, company, url, parser, pretty in specs:
        result = fetch(url, use_cache=True, timeout=60)
        entry = _fetch_entry(
            source_id, result.url, result.ok, result,
            provider_name=pretty, weblink=url, license="厂商官方价格页",
        )
        fetches.append(entry)
        if not result.ok:
            print(f"  ✗ {source_id:32} {result.error}", file=sys.stderr)
            continue
        parsed, parsed_warnings = parser(
            result.text, source_url=result.url, fetched_at=result.fetched_at,
            source_version=result.version,
        )
        for rec in parsed:
            rec.raw["company"] = company
            rec.raw["provider_name"] = pretty
            rec.raw["weblink"] = url
        records += parsed
        warnings += parsed_warnings
        entry["n_records"] = len(parsed)
        print(f"  ✓ {pretty:22} {len(parsed):4} 条")

    snapshots, snapshot_warnings, snapshot_fetches = china_official.load_verified_snapshots(
        CONFIG / "verified_official_prices.yaml"
    )
    snapshot_companies = {
        "volcengine_official_verified": "ByteDance / Doubao-Seed",
        "deepseek_official_verified": "DeepSeek",
        "google_video_official_verified": "Google",
        "google_omni_video_official_verified": "Google",
        "fal_google_video_verified": "Google",
        "runway_google_video_verified": "Google",
        "minimax_video_official_verified": "MiniMax",
        "runway_video_official_verified": "Runway",
        "luma_video_official_verified": "Luma AI",
        "vidu_video_official_verified": "ShengShu / Vidu",
        "pixverse_video_official_verified": "PixVerse",
        "alibaba_video_official_verified": "Alibaba / Qwen",
        "pruna_official_verified": "Pruna AI",
    }
    for rec in snapshots:
        rec.raw["company"] = snapshot_companies[rec.source]
        rec.raw["provider_name"] = next(
            item["provider_name"] for item in snapshot_fetches
            if item["source"] == rec.source
        )
        rec.raw["weblink"] = rec.source_url
    records += snapshots
    warnings += snapshot_warnings
    fetches += snapshot_fetches
    print(f"  ✓ 官方人工核验快照       {len(snapshots):4} 条")
    return records, fetches, warnings


def collect_sensenova() -> tuple[
    list, list[dict], list[str], dict[str, tuple[str, str]]
]:
    """Tier 1：商汤官方人民币价及 Token Plan 有限免费额度。"""
    records, fetches, warnings = [], [], []

    price_result = fetch(sensenova.PRICE_URL, use_cache=True, timeout=60)
    price_entry = _fetch_entry(
        "sensenova_official_html",
        price_result.url,
        price_result.ok,
        price_result,
        provider_name="SenseNova 官方价格",
        weblink=sensenova.PRICE_URL,
        license="厂商官方价格页",
    )
    fetches.append(price_entry)
    if price_result.ok:
        parsed, parsed_warnings = sensenova.parse_prices(
            price_result.text,
            source_url=price_result.url,
            fetched_at=price_result.fetched_at,
            source_version=price_result.version,
        )
        for rec in parsed:
            rec.raw["company"] = "SenseTime"
            rec.raw["provider_name"] = "SenseNova 官方价格"
            rec.raw["weblink"] = sensenova.PRICE_URL
        records += parsed
        warnings += parsed_warnings
        price_entry["n_records"] = len(parsed)
    else:
        print(f"  ✗ sensenova_official_html {price_result.error}", file=sys.stderr)

    model_result = fetch(sensenova.MODEL_LIST_URL, use_cache=True, timeout=60)
    model_entry = _fetch_entry(
        "sensenova_model_list_html",
        model_result.url,
        model_result.ok,
        model_result,
        provider_name="SenseNova 官方模型列表",
        weblink=sensenova.MODEL_LIST_URL,
        license="厂商官方模型文档",
    )
    fetches.append(model_entry)
    if model_result.ok:
        parsed, parsed_warnings = sensenova.parse_model_list_prices(
            model_result.text,
            source_url=model_result.url,
            fetched_at=model_result.fetched_at,
            source_version=model_result.version,
        )
        for rec in parsed:
            rec.raw["company"] = "SenseTime"
            rec.raw["provider_name"] = "SenseNova 官方模型列表"
            rec.raw["weblink"] = sensenova.MODEL_LIST_URL
        records += parsed
        warnings += parsed_warnings
        model_entry["n_records"] = len(parsed)
    else:
        print(f"  ✗ sensenova_model_list_html {model_result.error}", file=sys.stderr)

    plan_result = fetch(sensenova.TOKEN_PLAN_URL, use_cache=True, timeout=60)
    plan_entry = _fetch_entry(
        "sensenova_token_plan",
        plan_result.url,
        plan_result.ok,
        plan_result,
        provider_name="SenseNova Token Plan",
        weblink=sensenova.TOKEN_PLAN_URL,
        license="厂商官方套餐页",
    )
    fetches.append(plan_entry)
    free_tiers: dict[str, tuple[str, str]] = {}
    if plan_result.ok:
        free_tiers, plan_warnings = sensenova.parse_token_plan(
            plan_result.text, source_url=plan_result.url
        )
        warnings += plan_warnings
        plan_entry["n_records"] = len(free_tiers)
    else:
        print(f"  ✗ sensenova_token_plan {plan_result.error}", file=sys.stderr)

    print(
        f"  ✓ 商汤人民币官方价 {len(records):4} 条"
        f"；有限免费额度 {len(free_tiers):2} 个模型"
    )
    return records, fetches, warnings, free_tiers


def collect_ai302() -> tuple[list, list[dict], list[str]]:
    """Tier 3：302.AI 公开价格表中的讯飞转售价。"""
    r = fetch(ai302.PRICE_URL, use_cache=True, timeout=60)
    entry = _fetch_entry(
        "ai302_html", r.url, r.ok, r,
        provider_name="302.AI",
        weblink=ai302.WEBLINK,
        license="公开价格表",
    )
    if not r.ok:
        print(f"  ✗ ai302_html      {r.error}", file=sys.stderr)
        return [], [entry], []
    records, warnings = ai302.parse_prices(
        r.text, source_url=r.url, fetched_at=r.fetched_at,
        source_version=r.version,
    )
    entry["n_records"] = len(records)
    print(f"  ✓ 302.AI 讯飞转售价 {len(records):4} 条")
    return records, [entry], warnings


def collect_iflytek() -> tuple[list, list[dict], list[str]]:
    """Tier 1：讯飞星火 MaaS 官方公开 JSON 价格。"""
    r = fetch(
        iflytek.API_URL,
        use_cache=True,
        expect_content_type="application/json",
    )
    entry = _fetch_entry(
        "iflytek_maas_api", r.url, r.ok, r,
        provider_name="iFLYTEK 星火 MaaS 官方价格",
        weblink=iflytek.WEBLINK,
        license="厂商官方公开接口",
    )
    if not r.ok:
        print(f"  ✗ iflytek_maas_api {r.error}", file=sys.stderr)
        return [], [entry], []
    try:
        payload = json.loads(r.text)
    except json.JSONDecodeError as exc:
        return [], [entry], [f"iflytek_maas_api: JSON 解析失败：{exc}"]
    records, warnings = iflytek.parse_prices(
        payload, source_url=r.url, fetched_at=r.fetched_at,
        source_version=r.version,
    )
    for rec in records:
        rec.raw["company"] = "iFLYTEK"
        rec.raw["provider_name"] = "iFLYTEK 星火 MaaS 官方价格"
        rec.raw["weblink"] = iflytek.WEBLINK
    entry["n_records"] = len(records)
    print(f"  ✓ 讯飞人民币官方价 {len(records):4} 条")
    return records, [entry], warnings


def collect_upstage() -> tuple[list, list[dict], list[str]]:
    """Tier 1：Upstage 官方 API 定价页。"""
    r = fetch(upstage.PRICE_URL, use_cache=True)
    entry = _fetch_entry(
        "upstage_official_html", r.url, r.ok, r,
        provider_name="Upstage 官方价格",
        weblink=upstage.WEBLINK,
        license="厂商官方价格页",
    )
    if not r.ok:
        print(f"  ✗ upstage_official_html {r.error}", file=sys.stderr)
        return [], [entry], []
    records, warnings = upstage.parse_prices(
        r.text, source_url=r.url, fetched_at=r.fetched_at,
        source_version=r.version,
    )
    for rec in records:
        rec.raw["provider_name"] = "Upstage 官方价格"
        rec.raw["weblink"] = upstage.WEBLINK
    entry["n_records"] = len(records)
    print(f"  ✓ Upstage 官方价     {len(records):4} 条")
    return records, [entry], warnings


def collect_azure() -> tuple[list, list[dict]]:
    records, fetches = [], []
    url, pages, first = azure_retail.query_url(), 0, None
    while url and pages < AZURE_MAX_PAGES:
        r = fetch(url, use_cache=True)
        if first is None:
            first = _fetch_entry(
                "azure_retail", r.url, r.ok, r,
                provider_name="Azure AI Foundry",
                weblink="https://azure.microsoft.com/pricing/details/phi-3/",
                license="Azure 公开价格表",
            )
            fetches.append(first)
        if not r.ok:
            print(f"  ✗ azure_retail   {r.error}", file=sys.stderr)
            break
        try:
            payload = json.loads(r.text)
        except json.JSONDecodeError:
            break
        recs, _issues = azure_retail.parse_items(
            payload.get("Items", []), source_url=r.url,
            fetched_at=r.fetched_at, source_version=r.version,
        )
        for rec in recs:
            rec.raw["provider_name"] = "Azure AI Foundry"
            rec.raw["weblink"] = first["weblink"]
        records += recs
        url = payload.get("NextPageLink")
        pages += 1
    if first:
        first["n_records"] = len(records)
    print(f"  ✓ Azure Foundry  {len(records):4} 条")
    return records, fetches


def collect_price_apis() -> tuple[list, list[dict], list[str], dict]:
    """Tier 2/3：可直连的根源价格 API，全部由 config/price_apis.yaml 驱动。"""
    cfg = yaml.safe_load((CONFIG / "price_apis.yaml").read_text("utf-8"))
    records, fetches, warnings = [], [], []
    # {model_id: (平台名, 链接)}——哪些模型在某平台另有免费额度层
    free_tiers: dict[str, tuple[str, str]] = {}
    for spec in cfg["apis"]:
        r = fetch(spec["url"], use_cache=True)
        entry = _fetch_entry(
            spec["id"], r.url, r.ok, r,
            provider_name=spec.get("name") or spec["id"],
            weblink=spec.get("weblink") or "",
            license="公开 API",
        )
        fetches.append(entry)
        if not r.ok:
            print(f"  ✗ {spec['id']:16} {r.error_kind}: {r.error}", file=sys.stderr)
            continue
        try:
            payload = json.loads(r.text)
        except json.JSONDecodeError as exc:
            warnings.append(f"{spec['id']}: JSON 解析失败 {exc}")
            continue
        recs, warns, frees = price_apis.parse_api(
            payload, spec, source_url=r.url,
            fetched_at=r.fetched_at, source_version=r.version,
        )
        free_tiers.update(frees)
        # 归属公司；推断不出的丢弃，绝不硬塞——归错公司等于拆掉跨公司误配防线
        kept = []
        for rec in recs:
            company = infer_company(rec.model_id)
            if company:
                rec.raw["company"] = company
                kept.append(rec)
        records += kept
        warnings += warns
        entry["n_records"] = len(kept)
        print(f"  ✓ {spec.get('name', spec['id']):22} {len(kept):5} 条"
              f"（原始 {len(recs)}，{len(recs)-len(kept)} 条无法归属公司已丢弃）")
    if free_tiers:
        print(f"  ℹ️ {len(free_tiers)} 个模型另有免费额度层（不参与比价）")
    return records, fetches, warnings, free_tiers


def collect_nanogpt() -> tuple[list, list[dict], list[str]]:
    """Tier 3：nano-gpt 的图像模型转售价。

    单独成一个 collector 而不是配进 price_apis.yaml：它的 models.image 是
    以模型 id 为键的**嵌套字典**（通用适配器只吃列表），每个模型的 cost 又是
    以分辨率为键的字典，没有单一价格字段可指。详见 adapters/nanogpt.py。
    """
    result = fetch(nanogpt.NANOGPT_URL, use_cache=True, timeout=60)
    entry = _fetch_entry(
        "nanogpt", result.url, result.ok, result,
        provider_name="nano-gpt", weblink=nanogpt.NANOGPT_WEBLINK,
        license="第三方转售价目",
    )
    if not result.ok:
        print(f"  ✗ nanogpt {result.error}", file=sys.stderr)
        return [], [entry], []
    records, warnings = nanogpt.parse_nanogpt(
        result.text, source_url=result.url, fetched_at=result.fetched_at,
        source_version=result.version,
    )
    entry["n_records"] = len(records)
    print(f"  ✓ nano-gpt 图像转售价 {len(records):4} 条")
    return records, [entry], warnings


def collect_vendored(refresh: bool) -> tuple[list, list[dict]]:
    """Tier 2：MIT 开源数据集，优先读本地 vendor/ 副本。"""
    records, fetches = [], []
    targets = [
        ("modelsdev", vendored.MODELSDEV_URL, vendored.MODELSDEV_WEBLINK,
         "models.dev", "MIT", vendored.parse_modelsdev),
        ("litellm", vendored.LITELLM_URL, vendored.LITELLM_WEBLINK,
         "LiteLLM", "MIT", vendored.parse_litellm),
    ]
    for name, url, weblink, pretty, lic, parser in targets:
        payload = None if refresh else vendored.load_local(name)
        result = None
        from_local = payload is not None

        if payload is None:
            result = fetch(url, use_cache=True)
            if result.ok:
                try:
                    payload = json.loads(result.text)
                    vendored.save_local(name, result.text)
                except json.JSONDecodeError:
                    payload = None

        entry = _fetch_entry(
            name, url, payload is not None, result,
            provider_name=pretty, weblink=weblink, license=lic,
        )
        if from_local:
            entry["fetched_at"] = entry["fetched_at"] or ""
            entry["from_cache"] = True
        fetches.append(entry)

        if payload is None:
            print(f"  ✗ {pretty:16} 取不到（本地无副本且联网失败）", file=sys.stderr)
            continue

        fetched_at = (result.fetched_at if result else "") or _now()
        recs = parser(
            payload, source_url=url, fetched_at=fetched_at,
            source_version=(result.version if result else "local"),
        )
        for rec in recs:
            rec.raw["provider_name"] = pretty
            rec.raw["weblink"] = weblink
        records += recs
        entry["n_records"] = len(recs)
        origin = "本地 vendor/" if from_local else "已下载并存入 vendor/"
        print(f"  ✓ {pretty:22} {len(recs):5} 条（{origin}）")
    return records, fetches


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_input_capabilities(raw_models: list) -> dict:
    """从规格目录 + 官方核验表聚合全部已导出能力表的输入模态。"""
    target_functions = {
        "General-Purpose", "Coding", "Embedding", "Image Generation"
    }
    targets = [
        raw for raw in raw_models
        if raw.function in target_functions
        and raw.model not in export_mod.EXPORT_HIDDEN_MODELS
        and raw.company not in export_mod.EXPORT_HIDDEN_COMPANIES
    ]
    records = load_manual_modalities(CONFIG / "input_modalities.yaml", targets)
    modelsdev_payload = vendored.load_local("modelsdev")
    litellm_payload = vendored.load_local("litellm")
    if isinstance(modelsdev_payload, dict):
        records += parse_modelsdev_modalities(modelsdev_payload)
    if isinstance(litellm_payload, dict):
        records += parse_litellm_modalities(litellm_payload)

    # aliases.yaml 是给**价格**匹配写的，条目形如 `deepinfra: <id>`。这里复用
    # 同一张表匹配模态时必须先按源过滤：match_all 一旦命中别名就 continue，
    # 跳过 exact/contains。若别名指向的源（deepinfra/vercel/…）根本不产模态
    # 记录，而那个 model_id 又恰好出现在模态索引里，该模型就会既拿不到别名
    # 对应的模态记录、又失去正常匹配 —— 直接触发 100% 覆盖检查中止导出。
    # 实测：给 `flux-pro-1.1` 加 deepinfra 别名后，models.dev 里同名条目让
    # 别名命中，模态覆盖随即报「未覆盖 flux-pro-1.1」。
    modality_sources = {record.source for record in records}
    aliases = {
        model: kept
        for model, sources in load_aliases(CONFIG / "aliases.yaml").items()
        if (kept := {
            source: model_id
            for source, model_id in sources.items()
            if source in modality_sources
        })
    }
    report = match_all(targets, records, aliases)
    index = defaultdict(list)
    for record in records:
        index[(record.source, record.model_id)].append(record)
    by_model = defaultdict(list)
    for match in report.matches:
        by_model[match.raw_model] += index.get(
            (match.source, match.source_model_id), []
        )
    capabilities = {
        model: capability
        for model, matched in by_model.items()
        if (capability := select_capability(matched)) is not None
    }
    missing = [raw.model for raw in targets if raw.model not in capabilities]
    if missing:
        raise ValueError(
            "输入模态目录未覆盖以下模型：" + ", ".join(missing)
        )
    return capabilities


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--refresh-vendor", action="store_true",
                    help="强制重新下载 models.dev / LiteLLM 到 vendor/")
    args = ap.parse_args()

    print("== 1. 载入 raw.csv（只读）==")
    raw_models = load_raw(ROOT / "raw.csv")
    print(f"   {len(raw_models)} 个模型 / {len({r.company for r in raw_models})} 家公司")

    print("\n== 2. 抓取价格源 ==")
    records, fetches, warnings = [], [], []
    recs, fs, warns = collect_official_md()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_nonusd_official()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_china_official()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_image_official()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns, official_free_tiers = collect_sensenova()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_iflytek()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_ai302()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns = collect_upstage()
    records += recs; fetches += fs; warnings += warns
    for collector in (collect_aws, collect_azure):
        recs, fs = collector()
        records += recs; fetches += fs
    recs, fs, warns = collect_nanogpt()
    records += recs; fetches += fs; warnings += warns
    recs, fs, warns, free_tiers = collect_price_apis()
    records += recs; fetches += fs; warnings += warns
    # 官方 Token Plan 比第三方免费变体更接近模型原厂，存在同名时优先展示。
    free_tiers.update(official_free_tiers)
    recs, fs = collect_vendored(args.refresh_vendor)
    records += recs
    fetches += fs

    print("\n== 3. 获取 ECB 汇率并换算非美元报价 ==")
    fx_snapshot, fx_entry, fx_warnings = load_ecb_rates(
        OUT / "exchange_rates.json", save=not args.dry_run
    )
    converted, unsupported = convert_records_to_usd(records, fx_snapshot)
    fx_entry["n_records"] = converted
    fetches.append(fx_entry)
    warnings += fx_warnings
    if unsupported:
        warnings.append(
            "ecb_fx: ECB 当前没有以下币种的参考汇率，相关报价未换算："
            + ", ".join(sorted(unsupported))
        )
    if fx_snapshot:
        print(
            f"   ✓ ECB {fx_snapshot.as_of}：换算 {converted} 条非美元报价"
            + (f"；未支持 {', '.join(sorted(unsupported))}" if unsupported else "")
        )
    else:
        print("   ⚠️ ECB 汇率不可用；非美元报价保持原币种且不参与美元比价")

    print(f"\n   共 {len(records)} 条价格观测，来自 {len({r.source for r in records})} 个源")
    if warnings:
        print(f"   ⚠️ {len(warnings)} 条解析告警，详见 out/sources.md")

    print("\n== 4. 溯源完整性检查 ==")
    missing = [r for r in records
               if not r.source_url or not r.source_snippet or not r.unit_original]
    print(f"   缺溯源字段：{len(missing)} 条（必须为 0）")
    if missing:
        print("   ✗ 存在无法溯源的价格，这是 bug。", file=sys.stderr)
        return 1
    print(f"   ✓ 全部 {len(records)} 条均可追溯到 URL + 原文片段")

    print("\n== 5. 聚合输入模态 ==")
    input_capabilities = collect_input_capabilities(raw_models)
    modality_counts = defaultdict(int)
    for capability in input_capabilities.values():
        modality_counts[capability.modalities_cell] += 1
    print(
        f"   ✓ General-Purpose / Coding / Embedding / Image Generation 共 "
        f"{len(input_capabilities)} 个模型全部覆盖；组合分布 {dict(modality_counts)}"
    )

    print("\n== 6. 匹配价格到 raw.csv ==")
    report = match_all(raw_models, records, load_aliases(CONFIG / "aliases.yaml"))
    index = defaultdict(list)
    for rec in records:
        index[(rec.source, rec.model_id)].append(rec)
    by_model = defaultdict(list)
    for m in report.matches:
        by_model[m.raw_model] += index.get((m.source, m.source_model_id), [])

    best = {}
    for model, recs in by_model.items():
        pick = export_mod.pick_best(recs)
        if pick:
            best[model] = pick
    print(f"   匹配上 {len(best)}/{len(raw_models)} 个模型  方法分布 {report.method_counts}")

    if args.dry_run:
        print("\n[--dry-run] 未写任何文件。")
        return 0

    print("\n== 7. 导出 ==")
    OUT.mkdir(exist_ok=True)
    stats = export_mod.write_table(
        OUT / "models_with_prices.csv", raw_models, best, by_model, free_tiers,
        input_capabilities,
    )
    function_exports = (
        ("coding_models_with_prices.csv", {"Coding"}, True),
        # Embedding 表也包含 Rerank；两者都不生成文本 token，输出价只保留在
        # 底层观测中，不进入成品 CSV 和网页。
        ("embedding_models_with_prices.csv", {"Embedding"}, False),
    )
    function_stats = {}
    for filename, functions, include_output in function_exports:
        function_stats[filename] = export_mod.write_table(
            OUT / filename,
            raw_models,
            best,
            by_model,
            free_tiers,
            input_capabilities,
            export_functions=functions,
            token_prices_only=True,
            include_text_output_prices=include_output,
        )

    # ── 通用模型：开源权重且无任何报价 ──
    # 与图像/视频同一处理：权重可自取、任何源都查不到报价的模型，其
    # 价格是**客观不存在**而不是抓取失败。它们在主表里只能显示一片空白，
    # 排序和比价都没有意义；单独成表后，这批模型本身成为看点（有多少家
    # 公司、放出了多少参数量的权重），而主表的价格列不再被空行稀释。
    # 闭源却没拿到价的（not_found）是真缺口，性质不同，不进这张表。
    general_unpriced_stats = export_mod.write_table(
        OUT / "general_unpriced_open_weight_models.csv",
        raw_models,
        best,
        by_model,
        free_tiers,
        input_capabilities,
        price_mode=export_mod.PRICE_MODE_UNPRICED,
    )

    # ── 图像模型按结算单位拆三张表 ──
    # 图像生成的计价单位不统一（Gemini 系按 token、DALL·E 系按张），
    # 混在一张表里"最低价"会拿按张价去比按 token 价。拆表后每张表内
    # 官方价与最低价必定同单位。两种都有的模型同时进前两张表。
    image_exports = (
        ("image_token_models_with_prices.csv", export_mod.PRICE_MODE_TOKEN),
        ("image_per_image_models_with_prices.csv", export_mod.PRICE_MODE_IMAGE),
        # 开源权重且无任何官方/第三方报价——价格客观不存在，不是抓取失败
        ("image_unpriced_open_weight_models.csv", export_mod.PRICE_MODE_UNPRICED),
    )
    image_stats = {}
    for filename, mode in image_exports:
        image_stats[filename] = export_mod.write_table(
            OUT / filename,
            raw_models,
            best,
            by_model,
            free_tiers,
            input_capabilities,
            export_functions={"Image Generation"},
            price_mode=mode,
        )

    # ── 视频模型按结算单位拆四张表 ──
    # 同一模型可能同时按 token、每次调用、每段视频、每秒或每帧计价；这些
    # 量纲不互相覆盖。因此导出规则故意允许一个模型出现在多张表中。
    # 开源无报价表只收“权重开源且所有源均无价”的模型；开源但有托管报价的
    # 模型仍正常进入前三张价格表。
    video_exports = (
        ("video_token_models_with_prices.csv", export_mod.PRICE_MODE_TOKEN),
        (
            "video_per_generation_models_with_prices.csv",
            export_mod.PRICE_MODE_VIDEO_COUNT,
        ),
        (
            "video_per_second_frame_models_with_prices.csv",
            export_mod.PRICE_MODE_VIDEO_TIME,
        ),
        (
            "video_unpriced_open_weight_models.csv",
            export_mod.PRICE_MODE_UNPRICED,
        ),
    )
    video_stats = {}
    for filename, mode in video_exports:
        if mode in {
            export_mod.PRICE_MODE_VIDEO_COUNT,
            export_mod.PRICE_MODE_VIDEO_TIME,
        }:
            video_stats[filename] = export_mod.write_video_unit_table(
                OUT / filename,
                raw_models,
                by_model,
                input_capabilities,
                price_mode=mode,
            )
        else:
            video_stats[filename] = export_mod.write_table(
                OUT / filename,
                raw_models,
                best,
                by_model,
                free_tiers,
                input_capabilities,
                export_functions={"Video Generation"},
                price_mode=mode,
            )
    counts = defaultdict(int)
    for rec in records:
        counts[rec.source] += 1
    counts["sensenova_token_plan"] = len(official_free_tiers)
    # 汇率本身不是 PriceRecord，但“记录数”应显示本次实际换算了多少条报价。
    counts["ecb_fx"] = fx_entry["n_records"]
    export_mod.write_sources_md(OUT / "sources.md", fetches, counts, warnings)

    hidden = stats.get("hidden_by_function", 0)
    hidden_display = stats.get("hidden_by_display_rule", 0)
    shown = len(raw_models) - hidden - hidden_display
    print(f"   out/models_with_prices.csv   {stats.get('rows', 0)} 行"
          f"（{shown} 个模型，其中 {stats.get('multi_tier_models', 0)} 个有多档官方价）")
    if hidden:
        print(f"      ⚠️ 另有 {hidden} 个模型已抓取但未导出"
              f"（仅显示 {'/'.join(sorted(export_mod.EXPORT_FUNCTIONS))}）")
    if hidden_display:
        print(f"      ℹ️ 另有 {hidden_display} 个模型按展示规则隐藏"
              "（原始清单、抓取、匹配和数据源仍保留）")
    if stats.get("columns_dropped"):
        print(f"      {stats.get('columns', 0)} 列"
              f"（另有 {stats['columns_dropped']} 列全空已省略）")
    print(f"      有价格 {stats.get('got', 0)}（官方 {stats.get('official', 0)} / "
          f"托管 {stats.get('hosted', 0)}）")
    print(f"      权重免费无报价 {stats.get('weights_free', 0)}（价格不存在，非抓取失败）"
          f" · 真缺口 {stats.get('not_found', 0)}（闭源却没拿到价）")
    print(f"      权重：开源 {stats.get('w_free', 0)} / 闭源 {stats.get('w_proprietary', 0)}")
    print(
        f"   out/general_unpriced_open_weight_models.csv "
        f"{general_unpriced_stats.get('rows', 0)} 行"
        f"（通用模型 · 开源权重且无任何报价；价格客观不存在）"
    )
    for filename, functions, _include_output in function_exports:
        item = function_stats[filename]
        print(
            f"   out/{filename:<31} {item.get('rows', 0)} 行"
            f"（{'/'.join(sorted(functions))}；仅展示按 token 计费报价；"
            f"有价格 {item.get('got', 0)}）"
        )
    labels = {
        export_mod.PRICE_MODE_TOKEN: "按 token 结算",
        export_mod.PRICE_MODE_IMAGE: "按张结算",
        export_mod.PRICE_MODE_VIDEO_COUNT: "按次/按段视频结算",
        export_mod.PRICE_MODE_VIDEO_TIME: "按秒/按帧结算",
        export_mod.PRICE_MODE_UNPRICED: "开源权重且无任何报价",
    }
    for filename, mode in image_exports:
        item = image_stats[filename]
        detail = (
            f"有价格 {item.get('got', 0)}"
            if mode != export_mod.PRICE_MODE_UNPRICED
            else "价格客观不存在"
        )
        print(
            f"   out/{filename:<40} {item.get('rows', 0)} 行"
            f"（图像模型 · {labels[mode]}；{detail}）"
        )
    for filename, mode in video_exports:
        item = video_stats[filename]
        detail = (
            f"有价格 {item.get('rows', 0)} 行"
            if mode != export_mod.PRICE_MODE_UNPRICED
            else "价格客观不存在"
        )
        print(
            f"   out/{filename:<40} {item.get('rows', 0)} 行"
            f"（视频模型 · {labels[mode]}；{detail}）"
        )
    print(f"   out/sources.md               {len(fetches)} 个源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
