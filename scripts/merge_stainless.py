#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把不锈钢补充知识库合并进主知识库（cn/ru），并跑 validate_kb.py 校验。
用法:
    python scripts/merge_stainless.py              # 合并并校验
    python scripts/merge_stainless.py --dry-run    # 仅预览合并结果，不写盘
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
SCRIPTS = BASE / "scripts"

PAIRS = [
    (DATA / "cn_standards_final.json", DATA / "cn_stainless_standards.json"),
    (DATA / "ru_standards.json", DATA / "ru_stainless_standards.json"),
]


def merge(main_path: Path, add_path: Path) -> dict:
    main = json.loads(main_path.read_text(encoding="utf-8"))
    add = json.loads(add_path.read_text(encoding="utf-8"))
    existing = {s["id"] for s in main.get("standards", [])}
    added = 0
    for s in add.get("standards", []):
        if s["id"] in existing:
            print(f"  [SKIP] 已存在: {s['id']}")
            continue
        main["standards"].append(s)
        added += 1
    print(f"  [OK] {main_path.name} <- {add_path.name}：新增 {added} 项标准，"
          f"现共 {len(main['standards'])} 项")
    return main


def main():
    dry_run = "--dry-run" in sys.argv
    for main_path, add_path in PAIRS:
        if not main_path.exists() or not add_path.exists():
            print(f"  [WARN] 文件缺失，跳过: {main_path} / {add_path}")
            continue
        merged = merge(main_path, add_path)
        if not dry_run:
            main_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [WROTE] {main_path}")
    if not dry_run:
        print("\n== 校验 ==")
        import subprocess
        subprocess.run([sys.executable, str(SCRIPTS / "validate_kb.py"),
                        str(DATA / "cn_standards_final.json"),
                        str(DATA / "ru_standards.json")])


if __name__ == "__main__":
    main()
