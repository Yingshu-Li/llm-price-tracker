"""带条件缓存与失败隔离的抓取层。

设计要点：
- 跟随重定向，但记录**最终 URL**。溯源要的是"实际服务这段内容的地址"，
  而不是我们请求的地址（5 个官方 .md 里有 3 个会跳转）。
- ETag / Last-Modified 条件请求，304 时直接用本地缓存。
- 单源失败隔离：抓取失败返回 FetchError 而不是抛异常，让调用方能继续跑其他源。
- 识别企业代理拦截（本机在 Zscaler 后面），报成明确原因而非笼统网络错误。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
USER_AGENT = "llm-bench-tracker/0.1 (personal research; contact via repo)"


@dataclass
class FetchResult:
    ok: bool
    url: str  # 最终 URL（跟随重定向后）
    requested_url: str
    text: str = ""
    version: str | None = None  # ETag 或 Last-Modified
    fetched_at: str = ""
    from_cache: bool = False
    status: int | None = None
    error: str | None = None
    error_kind: str | None = None  # blocked_by_proxy | http_error | network | soft_404


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cache_paths(url: str) -> tuple[Path, Path]:
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    return CACHE_DIR / f"{key}.body", CACHE_DIR / f"{key}.meta.json"


def _load_cache(url: str) -> tuple[str | None, dict]:
    body_path, meta_path = _cache_paths(url)
    if not body_path.exists() or not meta_path.exists():
        return None, {}
    try:
        return body_path.read_text(encoding="utf-8"), json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, {}


def _save_cache(url: str, text: str, meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body_path, meta_path = _cache_paths(url)
    body_path.write_text(text, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2))


def _looks_proxy_blocked(text: str) -> bool:
    """本机在 Zscaler 后面，被拦时返回的是代理的说明页而非目标内容。

    误报的代价（把真实内容当成拦截）远高于漏报，所以只匹配明确的代理标识串。
    """
    head = text[:4000].lower()
    return any(
        marker in head
        for marker in ("zscaler", "your organization has blocked", "access denied by")
    )


def fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
    expect_content_type: str | None = None,
) -> FetchResult:
    """抓一个 URL，失败不抛异常而是返回 ok=False 的结果。

    expect_content_type: 若指定，响应 Content-Type 不以它开头则判为 soft_404。
    Cohere 和 AI21 的 /rss.xml 会返回 HTTP 200 + HTML，只看状态码会中招。
    """
    cached_text, cached_meta = _load_cache(url) if use_cache else (None, {})

    headers = {"User-Agent": USER_AGENT}
    if cached_text is not None:
        if etag := cached_meta.get("etag"):
            headers["If-None-Match"] = etag
        if last_mod := cached_meta.get("last_modified"):
            headers["If-Modified-Since"] = last_mod

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(url, headers=headers)

            if resp.status_code == 304 and cached_text is not None:
                return FetchResult(
                    ok=True,
                    url=cached_meta.get("final_url", url),
                    requested_url=url,
                    text=cached_text,
                    version=cached_meta.get("etag") or cached_meta.get("last_modified"),
                    fetched_at=_now_iso(),
                    from_cache=True,
                    status=304,
                )

            if resp.status_code >= 400:
                # 4xx 是确定性的，重试没有意义；5xx 才值得退避重试
                if resp.status_code < 500:
                    kind = (
                        "blocked_by_proxy"
                        if _looks_proxy_blocked(resp.text)
                        else "http_error"
                    )
                    return FetchResult(
                        ok=False,
                        url=str(resp.url),
                        requested_url=url,
                        status=resp.status_code,
                        fetched_at=_now_iso(),
                        error=f"HTTP {resp.status_code}",
                        error_kind=kind,
                    )
                last_error = f"HTTP {resp.status_code}"
                time.sleep(2**attempt)
                continue

            text = resp.text

            if _looks_proxy_blocked(text):
                return FetchResult(
                    ok=False,
                    url=str(resp.url),
                    requested_url=url,
                    status=resp.status_code,
                    fetched_at=_now_iso(),
                    error="响应内容是企业代理的拦截页，不是目标站点内容",
                    error_kind="blocked_by_proxy",
                )

            content_type = resp.headers.get("content-type", "")
            if expect_content_type and not content_type.startswith(expect_content_type):
                return FetchResult(
                    ok=False,
                    url=str(resp.url),
                    requested_url=url,
                    status=resp.status_code,
                    fetched_at=_now_iso(),
                    error=(
                        f"Content-Type 是 {content_type!r}，期望 "
                        f"{expect_content_type!r}（疑似 soft-404）"
                    ),
                    error_kind="soft_404",
                )

            version = resp.headers.get("etag") or resp.headers.get("last-modified")
            if use_cache:
                _save_cache(
                    url,
                    text,
                    {
                        "etag": resp.headers.get("etag"),
                        "last_modified": resp.headers.get("last-modified"),
                        "final_url": str(resp.url),
                        "content_type": content_type,
                        "cached_at": _now_iso(),
                    },
                )

            return FetchResult(
                ok=True,
                url=str(resp.url),
                requested_url=url,
                text=text,
                version=version,
                fetched_at=_now_iso(),
                status=resp.status_code,
            )

        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_RETRIES - 1:
                time.sleep(2**attempt)

    return FetchResult(
        ok=False,
        url=url,
        requested_url=url,
        fetched_at=_now_iso(),
        error=f"{MAX_RETRIES} 次尝试后仍失败：{last_error}",
        error_kind="network",
    )
