#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
从 PDF 原文件提取文本（用于核对标准条款/数值）
================================================
用法:
  python scripts/extract_pdf.py 中\GB-T\ 5223-2025.pdf
  python scripts/extract_pdf.py --all  # 提取 中\ 与 俄\ 下全部 PDF 到 pdf_text\
"""
import argparse
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / ".pippkgs"
sys.path.insert(0, str(PKG))
from pypdf import PdfReader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "pdf_text"


def extract_one(pdf: Path, out_dir: Path) -> Path:
    reader = PdfReader(str(pdf))
    if reader.is_encrypted:
        # 多为空密码（版权文档分发常见）；解密失败则报错
        try:
            reader.decrypt("")
        except Exception as e:  # noqa: BLE001
            print(f"    [decrypt] 空密码解密失败: {e}")
    text_parts = []
    for i, page in enumerate(reader.pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001
            t = f"[page {i} extract error: {e}]"
        text_parts.append(f"===== PAGE {i} =====\n{t}")
    text = "\n".join(text_parts)
    out = out_dir / (pdf.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*", help="PDF 路径；或 --all")
    ap.add_argument("--all", action="store_true", help="提取 中/ 与 俄/ 下全部 PDF")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    targets = []
    if args.all:
        for d in ("中", "俄"):
            for p in sorted((ROOT / d).glob("*.pdf")):
                targets.append(p)
    else:
        for a in args.pdfs:
            targets.append((ROOT / a).resolve())

    for t in targets:
        if not t.exists():
            print(f"  [SKIP] 不存在: {t}")
            continue
        try:
            o = extract_one(t, OUT)
            print(f"  [OK] {t.name} -> {o.name}")
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {t.name}: {e}")
    print(f"\ntext 输出目录: {OUT}")


if __name__ == "__main__":
    main()
