#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信推送模块 (push_wechat.py) —— 企业微信自建应用·应用消息
============================================================
按 docs/07 推荐路线：以【企业微信自建应用】为主推送通道（主动推送无条数限制，B2B 官方推荐）。

两种运行形态：
  1. dry-run（默认）—— 不真实发送，把将发送的负载打印出来。无凭证也能跑，用于联调/演示。
  2. real —— 调用企业微信 API 真实下发（需已安装 wechatpy 或配置基础 HTTP 封装）。

企业微信自建应用要点（docs/07）：
  - 应用消息 `POST /cgi-bin/message/send`，主动推送无条数限制，仅受频率限制。
  - 自建应用接口免费（2024.12 新政仅对第三方/代开发应用及客户联系接口计费）。
  - 认证 300 元/年；用户身份用 userid（企业微信 OAuth），无需手机号，隐私合规更优。

环境变量:
  WC_CORP_ID         企业微信 corpid
  WC_AGENT_SECRET    自建应用的 secret
  WC_AGENT_ID        自建应用 agentid（数字）
  WECHAT_DRY_RUN      任意非空则强制 dry-run（默认如果缺凭证也降级为 dry-run）

用法:
  python engine/push_wechat.py --userid zhangsan --title "合规评价完成" \
      --content "型号 DN50 环氧涂覆钢管：中国 A / 俄罗斯 C；Продукт ЕАС 需强制认证。"   # dry-run（默认）
  python engine/push_wechat.py --userid zhangsan --file report.md                      # 发送文件内容为文本消息
  python engine/push_wechat.py --userid zhangsan --title "..." --content "..." --real  # 真实发送
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def get_config():
    return {
        "corp_id": os.getenv("WC_CORP_ID", ""),
        "agent_secret": os.getenv("WC_AGENT_SECRET", ""),
        "agent_id": os.getenv("WC_AGENT_ID", ""),
        "dry_run": bool(os.getenv("WECHAT_DRY_RUN")),
    }


# ---------------------------------------------------------------------------
# 底层 HTTP（标准库 urllib，避免强依赖 wechatpy/requests）
# ---------------------------------------------------------------------------
def _http_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_access_token(corp_id: str, secret: str) -> str:
    if not corp_id or not secret:
        raise RuntimeError("缺少 WC_CORP_ID / WC_AGENT_SECRET，无法获取 access_token")
    url = (f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}"
           f"&corpsecret={secret}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errcode") != 0:
        raise RuntimeError(f"gettoken 失败: {data}")
    return data["access_token"]


# ---------------------------------------------------------------------------
# 发送企业微信应用消息
# ---------------------------------------------------------------------------
def send_wecom_app_message(userids, content, cfg=None, title=None, url=None,
                           msgtype="text", dry_run=None):
    """
    发送企业微信应用消息。
      msgtype: 'text' 文本 | 'textcard' 图文卡片（适合"评价摘要 + 查看报告"）
    返回: {"sent": bool, "mode": "dry-run"|"real", "payload": ..., "resp": ...}
    """
    cfg = cfg or get_config()
    is_dry = cfg["dry_run"] if dry_run is None else dry_run

    if msgtype == "text":
        message = {"msgtype": "text", "text": {"content": content}}
    else:  # textcard —— 企业微信 textcard.description 有长度限制，这里截断为摘要
        desc = content if len(content) <= 500 else content[:500] + "\n…（详见报告）"
        message = {"msgtype": "textcard",
                   "textcard": {"title": (title or "合规评价")[:64],
                                "description": desc,
                                "url": url or "https://example.com/report"}}

    payload = {
        "touser": "|".join(userids),
        "msgtype": message["msgtype"],
        "agentid": int(cfg["agent_id"]) if cfg["agent_id"] else 0,
        **message,
        "safe": 0,
    }

    # 缺凭证或缺 dry-run 标记 → 打印负载，不真实发送
    if is_dry or not cfg["corp_id"] or not cfg["agent_secret"] or not cfg["agent_id"]:
        print("[dry-run] 以下为将发送给企业微信应用的消息负载（未真实发送）：")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return {"sent": False, "mode": "dry-run", "payload": payload}

    token = _get_access_token(cfg["corp_id"], cfg["agent_secret"])
    url_api = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    resp = _http_json(url_api, payload)
    print("[real] sent:", json.dumps(resp, ensure_ascii=False))
    return {"sent": resp.get("errcode") == 0, "mode": "real",
            "payload": payload, "resp": resp}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="企业微信应用消息推送")
    ap.add_argument("--userid", action="append", required=True,
                    help="接收人 userid（可多次），如质检部负责人")
    ap.add_argument("--title", default="合规评价完成")
    ap.add_argument("--content", default="评价已生成，请查看详情。")
    ap.add_argument("--url", default="https://example.com/report")
    ap.add_argument("--msgtype", choices=["text", "textcard"], default="textcard")
    ap.add_argument("--file", help="把文件内容作为消息文本发送（优先于 --content）")
    ap.add_argument("--real", action="store_true", help="真实发送（需已配置企业微信凭证）")
    args = ap.parse_args()

    content = args.content
    if args.file:
        content = Path(args.file).resolve().read_text(encoding="utf-8")

    cfg = get_config()
    # 缺凭证时即使 --real 也降级为 dry-run，避免报错
    if not (cfg["corp_id"] and cfg["agent_secret"] and cfg["agent_id"]):
        cfg["dry_run"] = True

    send_wecom_app_message(
        args.userid, content, cfg=cfg, title=args.title, url=args.url,
        msgtype=args.msgtype, dry_run=(not args.real),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
