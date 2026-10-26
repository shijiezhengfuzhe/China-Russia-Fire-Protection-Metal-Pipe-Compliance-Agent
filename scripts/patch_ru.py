#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在服务器上给现有站点打「俄语报告」补丁（幂等、带备份、可回滚）。

作用：
  1. engine/report_ru.py         —— 新增文件（俄语报告渲染层）
  2. engine/llm_report.py        —— generate_report() 增加 lang=ru 分支
  3. engine/rag_chat.py          —— 问答版出报告时透传 lang
  4. engine/api_server.py        —— /api/evaluate 在 lang=ru 时把指标名俄文化

用法（在服务器上执行）：
    cd /opt/cnru-fire-pipe
    .venv/bin/python scripts/patch_ru.py            # 打补丁
    .venv/bin/python scripts/patch_ru.py --check    # 只检查，不修改
    .venv/bin/python scripts/patch_ru.py --revert   # 回滚到最近一次备份

前置：report_ru.py 必须已放到 engine/ 下。
"""
import argparse
import glob
import os
import py_compile
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENG = ROOT / "engine"
STAMP = time.strftime("%Y%m%d_%H%M%S")
TARGETS = ["llm_report.py", "rag_chat.py", "api_server.py"]

# (文件, 原片段, 新片段, 标记)
PATCHES = [
    (
        "llm_report.py",
        '    else:\n        report = _mock_report(product, result, lang)\n',
        '    elif lang == "ru":\n'
        '        # 俄语界面：整篇报告用俄文渲染（report_ru 只负责 lang=ru，不触碰中文路径）\n'
        '        import report_ru\n'
        '        report = report_ru.report_ru(product, result)\n'
        '    else:\n'
        '        report = _mock_report(product, result, lang)\n',
        'RU_REPORT_BRANCH',
    ),
    (
        "rag_chat.py",
        'report = LR.generate_report(product, res, LR.get_config())["report"]',
        'report = LR.generate_report(product, res, LR.get_config(), lang)["report"]',
        'RU_CHAT_LANG',
    ),
    (
        "api_server.py",
        '        result = E.evaluate(product, kb=get_kb())\n'
        '    except Exception as e:\n'
        '        raise HTTPException(status_code=500, detail=f"评价失败: {e}")\n'
        '    return _sanitize(result)',
        '        result = E.evaluate(product, kb=get_kb())\n'
        '        if (req.lang or "zh") == "ru":\n'
        '            result = _localize_ru(result)\n'
        '    except Exception as e:\n'
        '        raise HTTPException(status_code=500, detail=f"评价失败: {e}")\n'
        '    return _sanitize(result)',
        'RU_EVAL_LOCALIZE',
    ),
]

LOCALIZE_FN = '''
def _localize_ru(result: dict) -> dict:
    """lang=ru：把结构化判定里的中文指标名 / 条款名 / 分类名换成俄文。"""
    try:
        import report_ru as RR
        for key in ("cn_results", "ru_results"):
            for s in (result.get(key) or []):
                s["name_ru"] = RR.std_ru(s.get("standard")) or s.get("name_zh") or ""
                for it in (s.get("items") or []):
                    it["indicator_zh"] = RR.ind_ru(it.get("indicator_id"), it.get("indicator_zh") or "")
                    if it.get("clause"):
                        it["clause"] = RR.tr(it["clause"])
                    if it.get("category"):
                        it["category"] = RR.CAT_RU.get(it["category"], it["category"])
    except Exception:
        pass
    return result


@app.post("/api/evaluate")
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不修改")
    ap.add_argument("--revert", action="store_true", help="回滚到最近一次备份")
    args = ap.parse_args()

    if not ENG.is_dir():
        print("找不到 engine/ 目录，请在项目根目录（如 /opt/cnru-fire-pipe）下运行")
        return 1

    # --- 回滚 ---
    if args.revert:
        baks = sorted(glob.glob(str(ENG / "*.bak-*")))
        if not baks:
            print("没有备份可回滚")
            return 1
        latest = {}
        for b in baks:
            base = os.path.basename(b).split(".bak-")[0]
            if base not in latest or b > latest[base]:
                latest[base] = b
        for base, b in latest.items():
            shutil.copy2(b, ENG / base)
            print("已回滚 %s  <- %s" % (base, os.path.basename(b)))
        return 0

    # --- 前置检查 ---
    if not (ENG / "report_ru.py").exists():
        print("缺少 engine/report_ru.py —— 请先上传该文件")
        return 1

    todo = []
    for fname, old, new, tag in PATCHES:
        p = ENG / fname
        if not p.exists():
            print("跳过：找不到 %s" % fname)
            continue
        src = p.read_text(encoding="utf-8")
        if tag in src or (new.strip() and new.strip() in src):
            print("已打过补丁：%s" % fname)
            continue
        if old not in src:
            print("!! 未匹配到目标片段：%s —— 服务器上该文件与预期不同，跳过（请人工确认）" % fname)
            continue
        todo.append((p, src, old, new, fname))

    if args.check:
        print("\n--check：以上 %d 个文件待修改" % len(todo))
        return 0

    for p, src, old, new, fname in todo:
        bak = p.with_suffix(p.suffix + ".bak-" + STAMP)
        shutil.copy2(p, bak)
        src2 = src.replace(old, new, 1)
        # api_server：补 _localize_ru 函数定义（注意检查的是"定义"，不是调用）
        if fname == "api_server.py" and "def _localize_ru(" not in src2:
            src2 = src2.replace('@app.post("/api/evaluate")', LOCALIZE_FN.lstrip("\n"), 1)
        p.write_text(src2, encoding="utf-8")
        print("已修改 %s（备份 %s）" % (fname, bak.name))

    # --- 语法检查 ---
    ok = True
    for f in ["report_ru.py", "llm_report.py", "rag_chat.py", "api_server.py", "evaluate.py"]:
        p = ENG / f
        if not p.exists():
            continue
        try:
            py_compile.compile(str(p), doraise=True)
            print("  语法 OK  %s" % f)
        except Exception as e:
            ok = False
            print("  语法错误 %s : %s" % (f, e))
    if not ok:
        print("\n有语法错误，请执行 --revert 回滚")
        return 1

    print("\n补丁完成。重启服务： systemctl restart cnru-api")
    print("验证： curl -s -X POST http://127.0.0.1:8000/api/report "
          "-H 'Content-Type: application/json' "
          "-d '{\"product\":{\"type\":\"无缝钢管\"},\"lang\":\"ru\"}' | head -c 300")
    return 0


if __name__ == "__main__":
    sys.exit(main())
