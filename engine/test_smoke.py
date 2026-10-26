#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evaluate.py 冒烟测试：验证 TR CU 032 类别判定 + 全管线（迷你知识库）"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as E

print("========== A. ТР ТС 032/2013 类别判定 ==========")
cases = [
    # (P, DN, group, phase, 期望 in_scope, 期望 category)
    (1.6, 50, 2, "liquid", False, None),     # 典型消防水管 DN50 → 不适用
    (2.5, 300, 2, "liquid", True, "I"),      # 高压消防主干 DN300 → 类别I(声明)
    (0.8, 300, 2, "liquid", False, None),    # P≤1 → 不适用
    (1.0, 150, 2, "gas", True, "I"),         # 组2气体 P×DN=150 → 表7类别I
    (1.0, 100, 2, "gas", False, None),       # P×DN=100 未超100 → 不适用
    (3.0, 100, 1, "liquid", True, "II"),     # 组1液体 P×DN=300 → 表8类别II
    (0.8, 60, 1, "liquid", False, None),     # P×DN=48 ≤200 → 不适用
    (1.0, 200, 2, "liquid", False, None),    # DN=200 未超200 → 不适用
    (51.0, 300, 2, "liquid", True, "II"),    # P>50 → 表9类别II(认证)
    (6.0, 300, 2, "gas", True, "III"),       # 组2气体 DN>250,P>3.2 → 表7类别III(认证)
]
ok = True
for P, DN, g, ph, exp_scope, exp_cat in cases:
    r = E.tr_cu_032(P, DN, g, ph)
    good = (r["in_scope"] == exp_scope) and (r["category"] == exp_cat)
    ok &= good
    print(f"P={P} DN={DN} g{g}/{ph}: in_scope={r['in_scope']} cat={r['category']} "
          f"route={r['route']} | 期望 {exp_scope}/{exp_cat} | {'✅' if good else '❌'} | {r['note']}")
print("A 全部通过" if ok else "A 有失败 ✗")

print("\n========== B. 全管线（迷你知识库） ==========")
kb = {
    "cn": {"standards": [{
        "id": "GB/T 3091-2015", "name_zh": "低压流体输送用焊接钢管", "name_en": "",
        "year": 2015, "scope_zh": "低压流体用焊接钢管", "applicability_note": "消防镀锌管常用",
        "indicators": [
            {"id": "GB3091.MECH.Rm", "name_zh": "抗拉强度", "name_en": "Rm", "unit": "MPa",
             "category": "MECH", "grade_conditioned": True,
             "values": {"Q235B": {"min": 370}}, "clause": "5.4.1 表4", "test_method": "GB/T 228.1"},
            {"id": "GB3091.PROC.Hydro", "name_zh": "水压试验", "name_en": "Hydro", "unit": "MPa",
             "category": "PROC", "grade_conditioned": False,
             "values": {"min": 3.0}, "clause": "6.3", "test_method": "GB/T 241"},
            {"id": "GB3091.CHEM.P", "name_zh": "磷含量", "name_en": "P", "unit": "%",
             "category": "CHEM", "grade_conditioned": False,
             "values": {"max": 0.045}, "clause": "5.2.1 表3", "test_method": "GB/T 4336"},
        ]}]},
    "ru": {"standards": [{
        "id": "ГОСТ 3262-75", "name_zh": "水气钢管", "name_en": "", "year": 1975,
        "scope_zh": "水气钢管", "applicability_note": "消防给水钢管",
        "indicators": [
            {"id": "GOST3262.MECH.Re", "name_ru": "Предел текучести", "name_zh": "屈服强度",
             "unit": "MPa", "category": "MECH", "grade_conditioned": False,
             "values": {"min": 205}, "clause": "п.1.9", "test_method": "ГОСТ 1497"},
            {"id": "GOST3262.PROC.Hydro", "name_ru": "Гидравлическое испытание", "name_zh": "水压试验",
             "unit": "MPa", "category": "PROC", "grade_conditioned": False,
             "values": {"min": 2.5}, "clause": "п.2.7 таблица 7", "test_method": "ГОСТ 3845"},
            {"id": "GOST3262.COAT.Zinc", "name_ru": "Цинковое покрытие", "name_zh": "镀锌层",
             "unit": "g/m²", "category": "COAT", "grade_conditioned": False,
             "values": {"min": 273}, "clause": "п.2.12", "test_method": "ГОСТ 9.307"},
        ]}],
        "regulations": []},
}
product = json.loads(Path(__file__).resolve().parent.parent.joinpath(
    "examples", "sample_input.json").read_text(encoding="utf-8"))["product"]
result = E.evaluate(product, kb)
print(E.render_text(result))
print("\nB 完成（检查上表判定合理性）")
