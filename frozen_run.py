"""冻结网络快照跑流水线，用于「改动前后只差代码」的严格对照。

为什么需要它：上游源每天都在变。直接拿「上次提交的 out/」和「刚跑的 out/」
比对，会把**源漂移**和**我的代码改动**混在一起——实测一次这样的比对里，
image_per_image 表 105→86 行、主表 308→307 行，全都与被测改动无关。

用法：
    python frozen_run.py --record  <snap_dir> --out <dir>   # 抓一次并录下来
    python frozen_run.py --replay  <snap_dir> --out <dir>   # 用同一份快照重跑

录制/回放通过 monkeypatch ``src.http.fetch`` 实现，键为 URL。
回放时若遇到快照里没有的 URL 会**直接报错**而不是去联网——
静默回退到网络就等于这次对照白做了。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import src.http as http_mod  # noqa: E402


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def install(snap_dir: Path, mode: str) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    real_fetch = http_mod.fetch

    def fetch(url, *args, **kwargs):
        path = snap_dir / f"{_key(url)}.pkl"
        if mode == "replay":
            if not path.exists():
                raise RuntimeError(
                    f"回放时遇到快照中不存在的 URL：{url}\n"
                    "这次对照已不可信——请重新 --record 后再比。"
                )
            return pickle.loads(path.read_bytes())
        result = real_fetch(url, *args, **kwargs)
        path.write_bytes(pickle.dumps(result))
        (snap_dir / "index.json").write_text(
            json.dumps(sorted(p.name for p in snap_dir.glob("*.pkl")), indent=1),
            encoding="utf-8")
        return result

    http_mod.fetch = fetch
    # 各 collector 是 `from .http import fetch` 直接绑进模块命名空间的，
    # 只改 http_mod.fetch 不够，必须把已导入的引用一并替换。
    import update_prices as up
    if hasattr(up, "fetch"):
        up.fetch = fetch
    for name, mod in list(sys.modules.items()):
        if name.startswith("src.") and hasattr(mod, "fetch"):
            mod.fetch = fetch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", metavar="SNAP")
    ap.add_argument("--replay", metavar="SNAP")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if bool(args.record) == bool(args.replay):
        ap.error("--record 与 --replay 必须且只能选一个")

    snap = Path(args.record or args.replay)
    mode = "record" if args.record else "replay"

    import update_prices as up
    install(snap, mode)

    # 钉死时钟。本地 vendor/*.json 载入时 fetched_at 取的是 _now()，
    # 而 _sort_key 的第 7 位是「较新者优先」——跨秒运行会让 models.dev 与
    # LiteLLM 的胜负翻转，价格一模一样却换了 provider/source_url。
    # 实测：同代码同快照连跑两次，coding / embedding / 主表三张就会不一致。
    # 这是仓库既有问题（提交的 out/ 因此每次 CI 都无意义抖动），
    # 这里只在对照工装内钉死，不改产品代码行为。
    up._now = lambda: "2026-08-30T00:00:00+00:00"

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    up.OUT = out
    # 汇率快照也要固定，否则「今天的 ECB」会引入第二个变量
    sys.argv = ["update_prices.py"]
    code = up.main()
    print(f"\n[frozen_run] mode={mode} snap={snap} out={out} exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
