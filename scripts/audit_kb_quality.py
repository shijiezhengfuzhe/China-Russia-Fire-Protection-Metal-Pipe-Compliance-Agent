#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识库数据质量体检 (audit_kb_quality.py)
========================================
扫描 cn/ru 知识库，逐指标判断是否"可机读判定"（✅/❌）还是"需人工/LLM 判定"（◻️），
并标注含 [待核] 的项。输出：
  1) 汇总：各标准下 可判定 vs 需判定 的数量；
  2) 明细：需判定 + [待核] 的指标清单（含标准、指标、条款、values 形态）。

用途：定位"数据质量要补哪些"；补实后重跑本脚本，看 ◻️ 是否减少。

用法:
  python scripts/audit_kb_quality.py data/cn_standards_final.json data/ru_standards.json
  python scripts/audit_kb_quality.py data/cn_standards_final.json data/ru_standards.json --json out.json
"""
import argparse
import json
from pathlib import Path


def is_evaluable(values):
    """true = 引擎能数值/枚举/布尔判定；false = desc/table/map/spec → 需 LLM/认证机构。"""
    if not isinstance(values, dict):
        return True
    if any(k in values for k in ("desc", "table", "map", "spec")):
        return False
    if any(k in values for k in ("min", "max", "range", "set", "bool", "series")):
        return True
    # grade_conditioned: {牌号: {...}} → 递归看叶子；只要有任一分值可判定即视为可判定
    leaves = [v for v in values.values() if isinstance(v, (dict, list, int, float))]
    if not leaves:
        return True
    return all(is_evaluable(v) if isinstance(v, dict) else True for v in leaves)


def has_pending_verify(ind):
    blob = json.dumps(ind, ensure_ascii=False)
    return "待核" in blob or "[待核]" in blob


def classify(values):
    if not isinstance(values, dict):
        return "numeric"
    if "desc" in values:
        return "desc"
    if "table" in values:
        return "table"
    if "map" in values:
        return "map"
    if "spec" in values:
        return "spec"
    if "set" in values or "bool" in values:
        return "set/bool"
    if any(k in values for k in ("min", "max", "range", "series")):
        return "numeric"
    return "grade-cond"


def audit(kb_paths):
    grand = {"evaluable": 0, "needs_judgment": 0, "pending_verify": 0}
    rows = []
    per_std = []
    for p in kb_paths:
        kb = json.loads(Path(p).read_text(encoding="utf-8"))
        for s in kb.get("standards", []):
            e = n = pv = 0
            for ind in s.get("indicators", []):
                ev = is_evaluable(ind.get("values"))
                pv_flag = has_pending_verify(ind)
                if ev:
                    e += 1
                    grand["evaluable"] += 1
                else:
                    n += 1
                    grand["needs_judgment"] += 1
                if pv_flag:
                    pv += 1
                    grand["pending_verify"] += 1
                rows.append({
                    "file": Path(p).name,
                    "standard": s.get("id"),
                    "indicator": ind.get("name_zh"),
                    "indicator_id": ind.get("id"),
                    "clause": ind.get("clause"),
                    "values_form": classify(ind.get("values")),
                    "evaluable": ev,
                    "pending_verify": pv_flag,
                })
            per_std.append({
                "file": Path(p).name, "standard": s.get("id"),
                "evaluable": e, "needs_judgment": n, "pending_verify": pv,
                "indicators": e + n,
            })
    return grand, per_std, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kb_paths", nargs="+")
    ap.add_argument("--json", help="输出明细 JSON 到指定文件")
    args = ap.parse_args()

    grand, per_std, rows = audit(args.kb_paths)

    print("=" * 78)
    print(f"数据质量体检 | 文件: {', '.join(args.kb_paths)}")
    print("=" * 78)
    print(f"指标总数 {grand['evaluable'] + grand['needs_judgment']} | "
          f"可机读判定 ✅ {grand['evaluable']} | 需人工/LLM判定 ◻️ {grand['needs_judgment']} | "
          f"含[待核] {grand['pending_verify']}")
    print("-" * 78)
    print("【各标准】")
    for s in per_std:
        print(f"  {s['standard']:<38} 判定 {s['evaluable']:>3}  需判 {s['needs_judgment']:>3}  "
              f"[待核] {s['pending_verify']:>3}  (共{s['indicators']})")

    # 需判定 + [待核] 的明细
    focus = [r for r in rows if not r["evaluable"] or r["pending_verify"]]
    print("-" * 78)
    print(f"【需补实/需核验明细】共 {len(focus)} 项")
    for r in focus:
        mark = "<[待核]>" if r["pending_verify"] else "<需判定>"
        print(f"  {mark} [{r['standard']}] {r['indicator']}({r['indicator_id']}) "
              f"| {r['values_form']} | 条款 {r['clause']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": grand, "per_standard": per_std, "detail": rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入: {args.json}")
    return 0


if __name__ == "__main__":
    main()
