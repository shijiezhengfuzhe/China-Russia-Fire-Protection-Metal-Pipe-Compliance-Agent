#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验知识库 JSON 是否符合引擎 schema，并做基本一致性检查。
用法: python scripts/validate_kb.py data/cn_standards.json data/ru_standards.json
"""
import json, sys
from pathlib import Path

def check(path: Path) -> bool:
    ok = True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FAIL] {path}: JSON 解析错误 - {e}")
        return False
    stds = data.get("standards", [])
    if not stds:
        print(f"[WARN] {path}: 无 standards")
    ids = set()
    for s in stds:
        sid = s.get("id")
        if not sid:
            print(f"[FAIL] {path}: 存在无 id 的标准条目")
            ok = False
            continue
        if sid in ids:
            print(f"[FAIL] {path}: 标准 id 重复 {sid}")
            ok = False
        ids.add(sid)
        inds = s.get("indicators", [])
        if not inds:
            print(f"[WARN] {path}: 标准 {sid} 无指标")
        for ind in inds:
            iid = ind.get("id")
            vals = ind.get("values")
            if not iid:
                print(f"[FAIL] {path}: {sid} 中存在无 id 指标")
                ok = False
            if vals is None:
                print(f"[FAIL] {path}: {sid}/{iid} 缺 values")
                ok = False
            elif isinstance(vals, dict) and not any(k in vals for k in
                    ("min", "max", "range", "set", "desc", "bool")) and not vals:
                print(f"[FAIL] {path}: {sid}/{iid} values 为空 dict")
                ok = False
            if not ind.get("clause"):
                print(f"[WARN] {path}: {sid}/{iid} 缺 clause（条款号）")
    print(f"[{'OK' if ok else 'FAIL'}] {path}: {len(stds)} 项标准, 指标总数 "
          f"{sum(len(s.get('indicators', [])) for s in stds)}")
    return ok

def main():
    args = sys.argv[1:] or ["data/cn_standards_final.json", "data/ru_standards.json"]
    ok = all(check(Path(p)) for p in args)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
