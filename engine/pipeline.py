#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端管线 (pipeline.py)
========================
产品参数 → 规则引擎判定 → LLM 集体评价报告 →（可选）企业微信推送。

默认以"无凭证可运行"配置运行：LLM 用 mock、微信推送用 dry-run。
填好真实凭证后可用 --provider openai / --real 切换到真实链路。

用法:
  python engine/pipeline.py examples/sample_input_firepipe.json
      # 端到端（mock 报告 + dry-run 推送负载）

  python engine/pipeline.py examples/sample_input.json --out report.md
      # 生成报告并保存到 report.md

  python engine/pipeline.py examples/sample_input_firepipe.json --provider openai
      # 接入真实 LLM（需 LLM_API_KEY）

  python engine/pipeline.py examples/sample_input_firepipe.json --push --userid zhangsan
      # 生成报告后推送到企业微信（dry-run；--real 才真实发送）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as E          # noqa: E402
import llm_report as LR       # noqa: E402
import push_wechat as PW      # noqa: E402


def run(product_path: str, provider="mock", out=None, push=False,
        userid=None, msgtype="textcard", real=False,
        report_url="https://example.com/report") -> dict:
    raw = json.loads(Path(product_path).resolve().read_text(encoding="utf-8"))
    product = raw.get("product", raw)

    # 1) 规则引擎判定
    result = E.evaluate(product)

    # 2) LLM 集体评价报告（mock / openai）
    cfg = LR.get_config()
    cfg["provider"] = provider
    gen = LR.generate_report(product, result, cfg)
    report = gen["report"]

    if out:
        Path(out).write_text(report, encoding="utf-8")
        print(f"[pipeline] 报告已保存: {out}")

    # 3) 可选推送（企业微信应用消息，默认 dry-run）
    pushed = None
    if push:
        if not userid:
            print("[pipeline] --push 需指定 --userid，跳过推送。")
        else:
            wcfg = PW.get_config()
            if real and not (wcfg["corp_id"] and wcfg["agent_secret"] and wcfg["agent_id"]):
                wcfg["dry_run"] = True
            pushed = PW.send_wecom_app_message(
                [userid], report, cfg=wcfg, title="中俄消防管材合规评价",
                url=report_url, msgtype=msgtype, dry_run=(not real),
            )

    return {"engine_result": result, "report": report, "pushed": pushed}


def main():
    ap = argparse.ArgumentParser(description="端到端：评估→报告→(可选)推送")
    ap.add_argument("input", help="产品参数 JSON 文件路径")
    ap.add_argument("--provider", choices=["mock", "openai"], default=LR.get_config()["provider"])
    ap.add_argument("--out", help="报告保存路径（默认为空=打印到 stdout）")
    ap.add_argument("--push", action="store_true", help="评估后推送企业微信")
    ap.add_argument("--userid", help="接收人 userid（--push 时必填）")
    ap.add_argument("--msgtype", choices=["text", "textcard"], default="textcard")
    ap.add_argument("--real", action="store_true", help="真实发送企业微信（需凭证）")
    args = ap.parse_args()

    run(args.input, provider=args.provider, out=args.out, push=args.push,
        userid=args.userid, msgtype=args.msgtype, real=args.real)
    return 0


if __name__ == "__main__":
    sys.exit(main())
