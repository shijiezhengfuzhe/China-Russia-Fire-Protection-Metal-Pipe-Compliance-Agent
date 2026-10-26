#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 集体评价报告生成层 (llm_report.py)
=========================================
输入 : 产品参数 + 规则引擎 evaluate() 的结构化判定结果
输出 : 面向企业的"集体评价"报告（Markdown，可推送微信）

两种运行模式（可用环境变量/CLI 切换）：
  1. mock   —— 不调用网络，用规则引擎判定结果确定性生成一份结构化的"示例报告"。
               默认模式。无凭证也能跑，用于 MVP 演示与联调。
  2. openai —— 调用 OpenAI 兼容 /chat/completions（DeepSeek、Qwen、GLM、Kimi 等均支持）。
               需配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。

环境变量:
  LLM_PROVIDER      = mock(default) | openai
  LLM_BASE_URL      = 默认 https://api.deepseek.com/v1
  LLM_API_KEY       = 必填(openai 模式)
  LLM_MODEL         = 默认 deepseek-chat

用法:
  python engine/llm_report.py examples/sample_input_firepipe.json              # mock
  python engine/llm_report.py examples/sample_input_firepipe.json --provider openai
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as E  # noqa: E402


# ---------------------------------------------------------------------------
# 配置（环境变量可覆盖）
# ---------------------------------------------------------------------------
def get_config():
    return {
        "provider": os.getenv("LLM_PROVIDER", "mock"),
        "base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
    }


# ---------------------------------------------------------------------------
# 1. 组装提示词（复用 engine/agent_prompt.md）
# ---------------------------------------------------------------------------
def load_agent_prompt() -> str:
    p = Path(__file__).resolve().parent / "agent_prompt.md"
    return p.read_text(encoding="utf-8")


def build_prompt(product: dict, result: dict) -> tuple:
    """返回 (system_prompt, user_message)。"""
    system = load_agent_prompt()
    user = (
        "请对以下产品生成集体评价报告：\n"
        f"[产品参数]\n{json.dumps(product, ensure_ascii=False, indent=2)}\n\n"
        "[规则引擎判定 —— 请原样引用其中 verdict 与 clause，不得自行编造数值]\n"
        f"{json.dumps(result, ensure_ascii=False, indent=2, default=str)}"
    )
    return system, user


# ---------------------------------------------------------------------------
# 2. OpenAI 兼容调用（标准库 urllib，无额外依赖）
# ---------------------------------------------------------------------------
def _chat_completion(system: str, user: str, cfg: dict) -> str:
    if not cfg["api_key"]:
        raise RuntimeError("LLM_API_KEY 未配置；请设置环境变量或改用 --provider mock")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM 响应解析失败: {e}；响应={json.dumps(data, ensure_ascii=False)[:400]}")


# ---------------------------------------------------------------------------
# 3. Mock：由规则引擎判定结果确定性生成报告（无网络）
# ---------------------------------------------------------------------------
CAT_GROUPS = [
    ("尺寸/公差", ["尺寸", "外形", "重量", "偏差"]),
    ("牌号/品种", ["牌号", "品种", "级别", "等级", "直径范围"]),
    ("化学成分", ["化学", "成分"]),
    ("力学性能", ["力学", "抗拉", "屈服", "伸长", "延伸", "冲击", "破断"]),
    ("工艺/涂层", ["工艺", "水压", "液压", "压扁", "弯曲", "扩口", "卷边", "镀锌",
                   "涂覆", "涂层", "针孔", "附着", "盐雾", "老化", "连接", "螺纹"]),
    ("无损检测", ["无损", "探伤", "超声", "涡流", "漏磁"]),
    ("管理/认可", ["管理", "认可", "实验室"]),
    ("设计/认证", ["设计", "认证", "法规"]),
]


def cat_group(cat: str = "", name: str = "", ind_id: str = "") -> str:
    c = f"{cat or ''} {name or ''} {ind_id or ''}"
    # 顺序重要：先判无损（名称含无损/探伤/超声等），再化学/力学/尺寸，最后工艺/涂层
    if any(k in c for k in ("无损", "探伤", "超声", "涡流", "漏磁", "NDT")):
        return "无损检测"
    if any(k in c for k in ("牌号", "品种", "级别", "等级", "直径范围", "钢号", "分组")):
        return "牌号/品种"
    if any(k in c for k in ("化学", "成分", "碳", "硅", "锰", "磷", "硫", "铬", "镍", "钼")):
        return "化学成分"
    if any(k in c for k in ("力学", "抗拉", "屈服", "伸长", "延伸", "冲击", "破断", "强度")):
        return "力学性能"
    if any(k in c for k in ("尺寸", "外径", "壁厚", "长度", "偏差", "弯曲度", "重量", "公差", "精度")):
        return "尺寸/公差"
    if any(k in c for k in ("工艺", "水压", "液压", "压扁", "弯曲", "扩口", "卷边", "镀锌",
                           "涂覆", "涂层", "针孔", "附着", "盐雾", "老化", "连接", "螺纹", "耐蚀", "检验")):
        return "工艺/涂层"
    if any(k in c for k in ("管理", "认可", "实验室")):
        return "管理/认可"
    if any(k in c for k in ("设计", "认证", "法规")):
        return "设计/认证"
    return "其它"


def _cat_table(r: dict) -> dict:
    """返回 OrderedDict: 类别 -> {"cn":[总,通过,未通过,未适用], "ru":[...]}"""
    from collections import OrderedDict
    stats = OrderedDict()

    def add(side, results):
        for s in results:
            for it in s.get("items", []):
                g = cat_group(it.get("category"), it.get("indicator_zh"), it.get("indicator_id"))
                arr = stats.setdefault(g, {"cn": [0, 0, 0, 0], "ru": [0, 0, 0, 0]})[side]
                arr[0] += 1
                v = it.get("verdict")
                if v == "pass":
                    arr[1] += 1
                elif v == "fail":
                    arr[2] += 1
                else:
                    arr[3] += 1

    add("cn", r.get("cn_results", []))
    add("ru", r.get("ru_results", []))
    # 类别固定顺序
    order = [g for g, _ in CAT_GROUPS] + ["其它"]
    return OrderedDict((g, stats[g]) for g in order if g in stats)

# 中/俄牌号简要说明（用于报告"牌号说明"段）
CN_GRADE_DESC = {
    "Q195": "碳素结构钢；Q=屈服，195=屈服强度≥195 MPa，按 GB/T 700",
    "Q215A": "碳素结构钢，屈服≥215 MPa，A 级（不作冲击）",
    "Q215B": "碳素结构钢，屈服≥215 MPa，B 级（20℃冲击合格）",
    "Q235A": "碳素结构钢，屈服≥235 MPa，A 级（不作冲击）",
    "Q235B": "碳素结构钢；Q=屈服，235=屈服≥235 MPa，B 级（20℃冲击合格），最常用",
    "Q275A": "碳素结构钢，屈服≥275 MPa，A 级（不作冲击）",
    "Q275B": "碳素结构钢，屈服≥275 MPa，B 级（20℃冲击合格）",
    "Q355B": "低合金高强度结构钢（GB/T 1591），屈服≥355 MPa",
    "10": "优质碳素结构钢，碳约 0.07–0.13%，作流体输送无缝管",
    "20": "优质碳素结构钢，碳约 0.17–0.23%，流体输送无缝管最常用",
    "Q345D": "低合金高强度结构钢，屈服≥345 MPa，D 级（低温冲击）",
    "Q390D": "低合金高强度结构钢，屈服≥390 MPa，D 级",
    "Q420D": "低合金高强度结构钢，屈服≥420 MPa，D 级",
    "Q460D": "低合金高强度结构钢，屈服≥460 MPa，D 级",
    "HRB400": "热轧带肋钢筋，屈服≥400 MPa（混凝土用GB 1499.2）",
    "HRB400E": "抗震热轧带肋钢筋，屈服≥400 MPa，强屈比≥1.25",
    "HRB500": "热轧带肋钢筋，屈服≥500 MPa",
    "HRB500E": "抗震热轧带肋钢筋，屈服≥500 MPa",
    "06Cr19Ni10": "奥氏体不锈钢（近似304），碳≤0.06%、铬≈19%、镍≈10%",
    "022Cr19Ni10": "奥氏体不锈钢（304L，超低碳）",
    "06Cr17Ni12Mo2": "奥氏体不锈钢（316，含钼）",
    "022Cr17Ni12Mo2": "奥氏体不锈钢（316L，超低碳）",
    "06Cr18Ni11Ti": "奥氏体不锈钢（321，钛稳定化）",
}
RU_GRADE_DESC = {
    "Ст2сп": "ГОСТ 380 碳素结构钢；Ст2=第2号钢，сп=半镇静钢（近似对应 Q195/Q215）",
    "Ст2пс": "ГОСТ 380 碳素结构钢；Ст2，пс=半镇静钢",
    "Ст2кп": "ГОСТ 380 碳素结构钢；Ст2，кп=沸腾钢",
    "Ст3сп": "ГОСТ 380 碳素结构钢；Ст3=第3号钢，сп=半镇静钢（полуспокойная）",
    "Ст3пс": "ГОСТ 380 碳素结构钢；Ст3，пс=半镇静钢",
    "Ст3кп": "ГОСТ 380 碳素结构钢；Ст3，кп=沸腾钢（кипящая）",
    "Ст4сп": "ГОСТ 380 碳素结构钢；Ст4=第4号钢，сп=半镇静钢（近似对应 Q275）",
    "Ст4пс": "ГОСТ 380 碳素结构钢；Ст4，пс=半镇静钢",
    "Ст4кп": "ГОСТ 380 碳素结构钢；Ст4，кп=沸腾钢",
    "Ст20": "优质碳素钢（20号），按 ГОСТ 1050",
    "20": "优质碳素钢（20号），按 ГОСТ 1050",
    "10": "优质碳素钢（10号），按 ГОСТ 1050",
    "Ст10": "优质碳素钢（10号），按 ГОСТ 1050",
    "09Г2С": "低合金钢；Г=锰（约1.4–1.8%）、С=硅，低温韧性好",
    "17Г1С": "低合金锰钢（管线/容器用）",
    "14ХГС": "低合金钢（含铬/锰/硅）",
    "12Х18Н10Т": "奥氏体不锈钢；Х=铬≈18%、Н=镍≈10%、Т=钛稳定化",
    "08Х18Н10": "奥氏体不锈钢（近似08铬18镍10）",
    "08Х18Н10Т": "奥氏体不锈钢（钛稳定化，近似321）",
    "08Х17Н15М3Т": "奥氏体不锈钢（含钼，近似316L/316Ti）",
    "17Х18Н9": "奥氏体不锈钢（铬17镍9）",
    "10Х17Н13М2Т": "奥氏体不锈钢（含钼，近似316Ti）",
    "А400С": "热轧带肋钢筋（抗震），屈服≥400 MPa（对应HRB400）",
    "А500С": "热轧带肋钢筋（抗震），屈服≥500 MPa（对应HRB500）",
}
GRADE_MAP = {
    # —— 基本对应（人读/展示用）：取常用单一等效牌号，不是引擎匹配用的复合键。
    #    引擎 evaluate.CN_RU_GRADE_MAP 仍用复合键（如 Ст3кп/пс/сп）去匹配俄标牌号集合；
    #    这里仅用于 §二/§八 展示与"是否对应"判断，故用干净的 Ст3сп 等。
    "Q195": "Ст1сп", "Q215A": "Ст2сп", "Q215B": "Ст2сп", "Q215": "Ст2сп",
    "Q235A": "Ст3сп", "Q235B": "Ст3сп", "Q235": "Ст3сп",
    "Q275A": "Ст4сп", "Q275B": "Ст4сп", "Q275": "Ст4сп",
    "Q355B": "09Г2С", "Q345B": "09Г2С", "Q345D": "09Г2С", "Q390D": "09Г2С", "Q420D": "17Г1С", "Q460D": "14ХГС",
    "10": "10", "20": "20", "35": "35", "45": "45",
    "HRB400": "А400С", "HRB400E": "А400С", "HRB500": "А500С", "HRB500E": "А500С",
    # 不锈钢：与 evaluate.CN_RU_GRADE_MAP（GB/T 14976↔ГОСТ 9940/11068 权威映射）一致
    "06Cr19Ni10": "08Х18Н10", "06Cr19Ni10N": "08Х18Н10Т",
    "022Cr19Ni10": "08Х18Н10",
    "06Cr17Ni12Mo2": "10Х17Н13М2Т", "06Cr17Ni12Mo2N": "10Х17Н13М2Т",
    "022Cr17Ni12Mo2": "08Х17Н15М3Т",
    "06Cr18Ni11Ti": "08Х18Н10Т",
}


def _norm_ru(g: str) -> str:
    """俄牌号归一：把 Ст 组的 кп/пс/сп 脱氧后缀去掉（同一 Ст 组视为同族）。
    如 Ст3сп/Ст3пс/Ст3кп → 都归为 Ст3，避免被误判为"不对应"。"""
    if not g:
        return g
    for suf in ("кп", "пс", "сп"):
        if g.endswith(suf) and len(g) > len(suf):
            return g[:-len(suf)]
    return g


def _grade_note(cn: str, ru: str) -> str:
    cn = (cn or "").strip()
    ru = (ru or "").strip()
    lines = []
    lines.append("## 二、牌号简要说明")
    lines.append(f"- **中牌号 {cn}**：{CN_GRADE_DESC.get(cn, '（见对应 GB/T 标准的牌号要求）')}")
    lines.append(f"- **俄牌号 {ru}**：{RU_GRADE_DESC.get(ru, '（见对应 ГОСТ 标准的牌号要求）')}")
    # 对应关系（双向尝试）
    gmap = None
    if cn and cn in GRADE_MAP:
        mapped = GRADE_MAP[cn]
        if ru and _norm_ru(ru) == _norm_ru(mapped):
            gmap = f"{cn} ↔ {ru}（工程近似对应）"
        elif ru:
            gmap = f"⚠ {cn} 通常近似对应 {mapped}；但你填的俄牌号是 {ru}，两者不一致，请核实等效性"
        else:
            gmap = f"{cn} 通常近似对应 {mapped}（俄牌号未填，待定）"
    elif ru and ru in GRADE_MAP.values():
        rev = [k for k, v in GRADE_MAP.items() if v == ru]
        gmap = f"{ru} 近似对应 {rev[0] if rev else '相应中国牌号'}"
    if gmap:
        lines.append(f"- **中↔俄对应**：{gmap}。为工程近似，正式等效性以认可实验室判定为准。")
    return "\n".join(lines)


def _grade_mismatch(cn, ru):
    """中/俄牌号是否不对应：都填了、在 GRADE_MAP 有映射、但俄牌号 ≠ 映射值（归一 кп/пс/сп 后）→ 返回映射值；否则 None。"""
    cn = (cn or "").strip()
    ru = (ru or "").strip()
    if not cn or not ru:
        return None
    mapped = GRADE_MAP.get(cn)
    if mapped and _norm_ru(ru) != _norm_ru(mapped):
        return mapped
    return None


def _edge_gap(detail):
    """解析失败项的"差距比"。返回最小的差距比（越接近0越临界）；>0.05 或无法解析返回 None。
    支持 "X vs ≥Y（❌）" 复合项 与 "X ≥ Y" / "X ≤ Y" 简单项。"""
    import re
    if not detail:
        return None
    ratios = []
    for m in re.finditer(r"([\d.]+)\s*vs\s*(≥|≤)\s*([\d.]+)（❌）", detail):
        val = float(m.group(1)); op = m.group(2); req = float(m.group(3))
        if not req:
            continue
        r = (req - val) / req if op == "≥" else (val - req) / req
        ratios.append(max(r, 0.0))
    if ratios:
        return min(ratios)
    m = re.search(r"([\d.]+)\s*≥\s*([\d.]+)", detail)
    if m:
        req = float(m.group(2))
        return max((req - float(m.group(1))) / req, 0.0) if req else None
    m = re.search(r"([\d.]+)\s*≤\s*([\d.]+)", detail)
    if m:
        req = float(m.group(2))
        return max((float(m.group(1)) - req) / req, 0.0) if req else None
    return None


def _edge_short(detail):
    """从失败 detail 里只取"❌"的子项，用于简短展示（如 σв=410 vs ≥412（❌））。"""
    import re
    subs = re.findall(r"[^；]*（❌）", detail or "")
    return "；".join(subs) if subs else (detail or "")[:70]


def _na_short(detail):
    """从 NA 的 detail 里抽出「原因」与「参考」，用于把多个未评项压成一行展示。"""
    import re
    reason, hint = "", ""
    m = re.search(r"✋\s*(.*?)。📖", detail or "", re.S)
    if m:
        reason = m.group(1)
    m = re.search(r"💡\s*(.*?)最终结论", detail or "", re.S)
    if m:
        hint = m.group(1).strip()
    return reason, hint


# 壁厚要求（DN/公称口径 → 最小公称壁厚，mm）
CN_WALL_REQ = {6: 2.0, 8: 2.0, 10: 2.2, 15: 2.2, 20: 2.2, 25: 2.5, 32: 2.8, 40: 3.0,
               50: 3.2, 65: 3.2, 80: 3.2, 100: 3.5, 125: 3.5, 150: 3.5, 200: 4.0}  # GB/T 3091-2025 表1
RU_WALL_REQ = {6: 2.0, 8: 2.2, 10: 2.2, 15: 2.8, 20: 2.8, 25: 3.2, 32: 3.2, 40: 3.5,
               50: 3.5, 65: 4.0, 80: 4.0, 90: 4.0, 100: 4.5, 125: 4.5, 150: 4.5}  # ГОСТ 3262-75 普通型


def _wall_note(product: dict) -> str:
    dn = product.get("dn_mm")
    wt = product.get("wall_thickness_mm")

    def _chk(req):
        if req is None:
            return "未给出该口径要求"
        return "✅ 满足" if (wt is not None and wt >= req) else f"❌ 不足（需 ≥{req} mm）"

    L = ["## 一·六、壁厚对比"]
    if dn is None or wt is None:
        L.append("- （未提供 公称通径 或 壁厚，无法对比）")
    else:
        dn = int(dn)
        cn_req = CN_WALL_REQ.get(dn)
        ru_req = RU_WALL_REQ.get(dn)
        L.append(f"- 产品壁厚：**{wt} mm**（DN {dn}）")
        L.append(f"- 中国 GB/T 3091-2025 最小公称壁厚：{cn_req if cn_req else '未提供'} mm → {_chk(cn_req)}")
        L.append(f"- 俄罗斯 ГОСТ 3262-75（普通型）壁厚：{ru_req if ru_req else '未提供'} mm → {_chk(ru_req)}")
        L.append("> 注：CN 取 GB/T 3091-2025 表1 最小公称壁厚；RU 取 ГОСТ 3262-75 普通型壁厚。")
    return "\n".join(L)


def _hydro_note(product: dict) -> str:
    wt = product.get("wall_thickness_mm")
    od = product.get("od_mm")
    props = product.get("properties", {}) or {}
    hyd = props.get("hydrostatic_test_mpa")   # 产品实际液压试验压力
    re = props.get("re_mpa")                  # 下屈服强度（作为允许应力基础近似）

    # CN GB/T 3091-2025 公式(3)：P = 2×t×S/D，S=0.6×上屈服(近似用ReL)，最大5.0MPa，修约到0.5
    cn_req = None
    if wt and od and re:
        S = 0.6 * re
        p = 2 * wt * S / od
        p = min(p, 5.0)
        cn_req = round(p * 2) / 2  # 修约到 0.5 MPa
    ru_req = 2.4                    # ГОСТ 3262-75 普通型

    def _chk(prod, req):
        if prod is None:
            return "未提供"
        if req is None:
            return "—"
        return "✅ 满足" if prod >= req else f"❌ 不足（需≥{req} MPa）"

    L = ["## 一·七、液压试验压力对比"]
    L.append(f"- 产品液压试验压力：**{hyd if hyd is not None else '未提供'} MPa**")
    L.append(f"- 中国 GB/T 3091-2025（公式3，最大5.0MPa）：{cn_req if cn_req is not None else '未给出'} MPa → {_chk(hyd, cn_req)}")
    L.append(f"- 俄罗斯 ГОСТ 3262-75（普通型）：{ru_req} MPa → {_chk(hyd, ru_req)}")
    L.append("> 注：CN 按公式 P=2×t×S/D（S=0.6×上屈服强度），最大5.0MPa；RU 取普通型 2.4MPa。")
    return "\n".join(L)


def _detail_table(product: dict, result: dict = None) -> dict:
    """尺寸与工艺关键指标对比表（中国 vs 俄罗斯）。

    壁厚/液压等"判定"列不再各自重算，而是直接读取引擎 result 的权威判定，
    确保 §三 与 §四/§五 口径一致（避免同项一处 ✅/❌、另一处 NA 的矛盾）。
    """
    props = product.get("properties", {}) or {}
    # 引擎判定（权威）：扁平化两侧指标项，供 §三 按 id 后缀取数
    _cn_items, _ru_items = [], []
    if result:
        for s in result.get("cn_results", []):
            _cn_items.extend(s.get("items", []))
        for s in result.get("ru_results", []):
            _ru_items.extend(s.get("items", []))
    def _pick(results, suffixes):
        for it in results:
            if str(it.get("indicator_id", "")).split(".")[-1].upper() in suffixes:
                return it
        return None
    def _cell(it, na_extra=""):
        if it is None:
            return "—"
        v = it["verdict"]
        sym = {"pass": "✅", "fail": "❌", "na": "◻️"}[v]
        lbl = {"pass": "满足", "fail": "不达标", "na": (na_extra or "未判定（见 §四/五）")}[v]
        return f"{sym} {lbl}"
    dn = product.get("dn_mm")
    od = product.get("od_mm")
    wt = product.get("wall_thickness_mm")
    ln = product.get("length_m")
    zinc = props.get("zinc_coat_g_m2")
    zinc_um = props.get("zinc_coat_thickness_um")
    if zinc_um is None and zinc is not None:
        zinc_um = round(zinc / 7.14, 1)
    elif zinc is None and zinc_um is not None:
        zinc = round(zinc_um * 7.14, 1)
    hydro = props.get("hydrostatic_test_mpa")
    re = props.get("re_mpa")
    threaded = product.get("threaded")
    connection = product.get("connection") or "平口/未注明"
    bend = props.get("bending")
    flat = props.get("flattening")
    ptype = (product.get("type") or "")
    coat_thk = props.get("coating_thickness_mm")
    coat_adh = props.get("coating_adhesion_grade")
    is_coat = "消防涂覆" in ptype or "涂覆" in ptype

    # 各管材类型的"基管"标准（用于 §三 尺寸与工艺对比的 CN/RU 参照）。
    # 不同管材适用不同标准；按"由具体到一般"顺序匹配（先不锈钢/涂覆，再无缝/直缝）。
    _BASE = {
        "不锈钢无缝": ("GB/T 14976-2012", "ГОСТ 9940-81"),
        "不锈钢焊接": ("GB/T 12771-2019", "ГОСТ 11068-81"),
        "消防涂覆": ("GB/T 5135.20-2010", "СП 10.13130.2020"),
        "涂覆": ("GB/T 5135.20-2010", "СП 10.13130.2020"),
        "无缝": ("GB/T 8163-2018", "ГОСТ 8731-87"),
        "直缝": ("GB/T 13793-2016", "ГОСТ 10705-80"),
        "镀锌": ("GB/T 3091-2015", "ГОСТ 3262-75"),
        "黑管": ("GB/T 3091-2015", "ГОСТ 3262-75"),
    }
    _cbase, _rbase = "GB/T 3091-2015", "ГОСТ 3262-75"
    for _k, (_c, _r) in _BASE.items():
        if _k in ptype:
            _cbase, _rbase = _c, _r
            break

    RU_OD = {6:10.2,8:13.5,10:17.0,15:21.3,20:26.8,25:33.5,32:42.3,40:48.0,50:60.0,65:75.5,80:88.5,90:101.3,100:114.0,125:140.0,150:165.0}
    RU_WALL = {6:2.0,8:2.2,10:2.2,15:2.8,20:2.8,25:3.2,32:3.2,40:3.5,50:3.5,65:4.0,80:4.0,90:4.0,100:4.5,125:4.5,150:4.5}
    CN_WALL = {6:2.0,8:2.0,10:2.2,15:2.2,20:2.2,25:2.5,32:2.8,40:3.0,50:3.2,65:3.2,80:3.2,100:3.5,125:3.5,150:3.5,200:4.0}
    R = []

    def _ge(v, req, unit=""):
        if v is None:
            return "未提供"
        return "✅ 满足" if v >= req else f"❌ 不足(需≥{req}{unit})"
    def _range(v, lo, hi, unit=""):
        if v is None:
            return "未提供"
        return "✅" if lo <= v <= hi else f"❌(需 {lo}~{hi}{unit})"
    def _near(v, t, tol_ratio=0.01):
        if v is None:
            return "未提供"
        return "✅" if abs(v - t) / t <= tol_ratio else f"❌(标准值为 {t})"

    # 液压试验压力（CN 公式3 / RU 普通型）
    cn_hydro = None
    if wt and od and re:
        cn_hydro = round(min(2 * wt * 0.6 * re / od, 5.0) * 2) / 2

    table = []
    # 焊接/镀锌管：俄罗斯侧按 ГОСТ 3262 的 Ду→OD 对照表校验；无缝/不锈钢按 GB/T 17395 系列，
    # 名义外径即为标准值，无需套用 3262 表 → 只提示"需实测核对"。
    _is_welded_galv = ("镀锌" in ptype) or ("焊接钢管" in ptype) and ("无缝" not in ptype)
    if _is_welded_galv:
        _od_ru_ref = f"{_rbase}：Ду{int(dn) if dn else '?'} → OD {RU_OD.get(int(dn))} mm" if dn else "（未给DN）"
        _od_ru_v = _near(od, RU_OD.get(int(dn))) if dn and RU_OD.get(int(dn)) else "未提供"
    else:
        _od_ru_ref = f"{_rbase}：外径按标准系列，名义外径 {od if od else '?'} mm（允许偏差见 17395）"
        _od_ru_v = "（名义值无误，需实测偏差）" if od else "未提供"
    table.append(("外径（D）",
                  f"{_cbase} / GB/T 17395 系列；允许偏差普通级 ±1.0%D",
                  "（由实测核对）",
                  _od_ru_ref,
                  _od_ru_v))
    if result is not None:
        _cn_wt_v = _cell(_pick(_cn_items, {"WT"}), "未判定（壁厚等级/表，见 §四）")
        _ru_wt_v = _cell(_pick(_ru_items, {"WALL"}), "未判定（轻型/普通/加强型，见 §五）")
    else:
        _cn_wt_v = _ge(wt, CN_WALL.get(int(dn), 0), " mm") if dn else "未提供"
        _ru_wt_v = _ge(wt, RU_WALL.get(int(dn), 0), " mm") if dn else "未提供"
    table.append(("壁厚（t）",
                  f"{_cbase} 最小公称壁厚 {CN_WALL.get(int(dn), '—')} mm；允许偏差见 GB/T 17395",
                  _cn_wt_v,
                  f"{_rbase} 普通型 {RU_WALL.get(int(dn), '—')} mm",
                  _ru_wt_v))
    table.append(("长度（L）",
                  "GB/T 17395 定尺 0~+20mm",
                  _range(ln, ln, ln + 0.02, " m") if ln else "未提供",
                  f"{_rbase} 4~12 m（非定尺）",
                  _range(ln, 4, 12, " m")))
    table.append(("弯曲度",
                  f"{_cbase}：OD≥114 全长 ≤0.2%L",
                  "（需实测）",
                  f"{_rbase} 检查曲率",
                  "（需实测）"))
    table.append(("单位长度重量",
                  f"W=0.0246615×t×(D−t) kg/m"
                  + (f"；理论≈{0.0246615*wt*(od-wt):.2f} kg/m" if wt and od else ""),
                  "（与实测核对）",
                  f"按 {_rbase} 尺寸计算",
                  "（与实测核对）"))
    # 镀锌层：仅"镀锌/黑管"类基管才有镀锌层要求（GB/T 3091 / ГОСТ 3262）；
    # 无缝/不锈钢/涂覆基管无镀锌层规定，仅当用户填了镀锌值才提示非标规定。
    _zinc_cn_ref = "GB/T 3091 5.9 镀锌层平均重量 ≥300 g/m²（≈42.0 μm，OD≤168.3）"
    _zinc_ru_ref = "ГОСТ 3262 2.7 镀锌层厚度 ≥30 μm（≈214 g/m²，整管连续）"
    if ("镀锌" not in ptype) and ("黑管" not in ptype):
        # 无缝/不锈钢等：镀锌层并非其基管标准要求
        _zinc_cn_ref = f"{_cbase}：无镀锌层规定"
        _zinc_ru_ref = f"{_rbase}：无镀锌层规定"
        _zinc_cn_v = _zinc_ru_v = "（非镀锌规格，不适用）"
    elif zinc is None and zinc_um is None:
        _zinc_cn_v, _zinc_ru_v = "（未提供）", "（未提供）"
    else:
        # 中国侧：以"重量"为准；给出厚度换算对照
        _cn_ok = (zinc is not None and zinc >= 300)
        _cn_txt = ("✅ 满足" if _cn_ok else "❌ 不足(需≥300 g/m²)")
        _cn_txt += f"（重量 {zinc} g/m²" + (f"≈厚度 {zinc_um} μm" if zinc_um else "") + "）" if zinc is not None else "（未提供）"
        # 俄罗斯侧：以"厚度"为准；给出重量换算对照
        _ru_ok = (zinc_um is not None and zinc_um >= 30)
        _ru_txt = ("✅ 满足" if _ru_ok else "❌ 不足(需≥30 μm)")
        _ru_txt += f"（厚度 {zinc_um} μm" + (f"≈重量 {zinc} g/m²" if zinc else "") + "）" if zinc_um is not None else "（未提供）"
        _zinc_cn_v, _zinc_ru_v = _cn_txt, _ru_txt
    table.append(("镀锌层（重量 ⇄ 厚度）",
                  _zinc_cn_ref, _zinc_cn_v,
                  _zinc_ru_ref, _zinc_ru_v))
    # 管螺纹：仅"采用螺纹连接"、且基管标准含螺纹要求的类型才列入（GB/T 3091 / ГОСТ 3262）。
    # 无缝/不锈钢/直缝等以焊接/法兰/平口连接为主，其标准不规定螺纹 → 此行不适用。
    _has_thread_std = ("GB/T 3091" in _cbase) or ("GB/T 13793" in _cbase)
    _thread_requires = _has_thread_std and threaded and (dn is None or int(dn) <= 150)
    if not _has_thread_std:
        _th_cn_v = _th_ru_v = "（以焊接/法兰/平口连接为主，本表不评定螺纹；如采用螺纹按 GB/T 7306 / ГОСТ 6357）"
    elif _thread_requires:
        _th_cn_v = "✅ 符合（GB/T 7306.1/7306.2 55°密封管螺纹）"
        _th_ru_v = "✅ 符合（ГОСТ 6357-81 圆柱管螺纹）"
    elif threaded:
        _th_cn_v = "⚠ 有螺纹但 DN>150（超 5.8 螺纹连接范围）"
        _th_ru_v = "⚠ DN>150 超 ГОСТ 6357 常用范围"
    else:
        _th_cn_v = _th_ru_v = "（无螺纹：平口/焊接/法兰连接，不适用）"
    if not _has_thread_std:
        _th_cn_ref = f"{_cbase}：不规定管端螺纹（以焊接/法兰/平口连接为主）"
        _th_ru_ref = f"{_rbase}：不规定管端螺纹"
    else:
        _th_cn_ref = f"{_cbase} 5.8（螺纹连接）；GB/T 7306.1/7306.2（55°密封管螺纹）"
        _th_ru_ref = f"{_rbase} 2.5；ГОСТ 6357-81（圆柱管螺纹）"
    table.append(("管螺纹",
                  _th_cn_ref,
                  _th_cn_v,
                  _th_ru_ref,
                  _th_ru_v))
    # 连接方式：产品选定的连接类型，与标准允许的连接方式集合比对（中/俄逐侧）。
    _CONN_CN = ["沟槽连接", "螺纹连接", "法兰连接", "焊接", "卡压连接"]
    _CONN_RU = {"沟槽连接": "жёлобчатое", "螺纹连接": "резьбовое", "法兰连接": "фланцевое",
                "焊接": "сварное", "卡压连接": "пресс-фитинговое"}
    _cn_conn_ok = connection in _CONN_CN and connection != "平口/未注明"
    _ru_conn_ok = connection in _CONN_RU
    _conn_cn_v = ("✅ 允许（" + connection + "）") if _cn_conn_ok else "（未注明/不在标准列名内）"
    _conn_ru_v = ("✅ 允许（" + _CONN_RU.get(connection, connection) + "）") if _ru_conn_ok else "（未注明/需按设计规范）"
    table.append(("连接方式",
                  f"{_cbase}；消防管按 GB 50084/GB 50261：沟槽/螺纹/法兰/焊接",
                  _conn_cn_v,
                  f"{_rbase}：сварное（焊接）/фланцевое（法兰）/резьбовое（螺纹）/жёлобчатое（沟槽）/пресс-фитинговое（压接）",
                  _conn_ru_v))
    # 尺寸相关要求（外径/壁厚/长度公差）汇总行——并列中国（GB/T 17395）与俄罗斯（ГОСТ 尺寸表）要求，
    # 并给出名义值与"需实测偏差"提示。
    _od_n = od
    _wt_n = wt
    _ln_n = ln
    _od_meas = props.get("od_meas_mm")
    _wt_meas = props.get("wt_meas_mm")
    def _dim_note(v, unit="mm"):
        return ("需实测" if v is None else f"实测 {v} {unit}")
    _dim_cn = (f"外径按 GB/T 17395 系列；允许偏差普通级 ±1.0%D（名义 {_od_n} mm）"
               + (f"，实测 {_od_meas} mm（偏差 {abs(_od_meas-_od_n)/_od_n*100:.2f}%）" if _od_meas and _od_n else ""))
    _dim_ru = (f"外径按 {_rbase} 尺寸表；名义 {_od_n} mm"
               + (f"，实测 {_od_meas} mm" if _od_meas else "，允许偏差见标准"))
    _dim_cn_v = ("✅ 供参考（需实测核对）" if _od_meas else "（名义值；公差需实测）")
    _dim_ru_v = ("✅ 供参考（需实测核对）" if _od_meas else "（名义值；公差需实测）")
    table.append(("尺寸相关要求（外径/壁厚/长度公差）",
                  _dim_cn,
                  _dim_cn_v,
                  _dim_ru,
                  _dim_ru_v))
    # 无损检测（可选/按协议，非强制）
    ndt = props.get("ndt")
    ndt_cn = f"{_cbase}：可用涡流/超声/漏磁**代替液压**（可选，按协议）"
    ndt_ru = f"{_rbase}：**未规定** NDT（以水压+表面/尺寸检验为主）"
    table.append(("无损检测（NDT，可选/按协议，非强制）",
                  ndt_cn,
                  (f"{'✅ 合格' if str(ndt).lower()=='pass' else ('❌ 不合格' if str(ndt).lower()=='fail' else '（未提供）')}" + "；可选项，仅作附加信息不判不合规") if ndt else "（可选，未提供）",
                  ndt_ru,
                  "—"))
    if result is not None:
        _cn_hyd_v = _cell(_pick(_cn_items, {"HYD"}), "未判定（公式/需实测试验压力，见 §四）")
        _ru_hyd_v = _cell(_pick(_ru_items, {"HYDRO", "HYD"}), "未判定（按管壁等级，见 §五）")
    else:
        _cn_hyd_v = _ge(hydro, cn_hydro or 0, " MPa") if cn_hydro else "未提供"
        _ru_hyd_v = _ge(hydro, 2.4, " MPa")
    table.append(("液压试验压力",
                  f"{_cbase} 公式3，最大5.0MPa"
                  + (f"；本管≈{cn_hydro} MPa" if cn_hydro else ""),
                  _cn_hyd_v,
                  f"{_rbase} 普通型 2.4 MPa",
                  _ru_hyd_v))
    # 弯曲/压扁
    test = "弯曲" if (od is not None and od <= 60.3) else "压扁"
    table.append(("弯曲/压扁（6.6）",
                  f"{_cbase}：{test}后无裂纹",
                  _ge(1 if (bend if od is not None and od <= 60.3 else flat) == "pass" else None, 1) if (bend if od is not None and od <= 60.3 else flat) else "未提供",
                  f"{_rbase}：未规定",
                  "—"))
    # 涂覆钢管：按 GB/T 5135.20 增加"涂层"关键对比
    if is_coat:
        def _chk_coat_thk(v):
            if v is None:
                return "未提供"
            req = 0.35 if (dn is not None and int(dn) >= 80) else 0.30
            return "✅ 满足" if v >= req else f"❌ 不足(需≥{req}mm)"
        def _chk_adh(v):
            if v is None:
                return "未提供"
            return "✅" if 1 <= v <= 3 else "❌(需1~3级)"
        table.append(("涂层厚度",
                      "GB/T 5135.20 5.4 表1：DN≤65 >0.30mm；DN≥80 >0.35mm",
                      _chk_coat_thk(coat_thk),
                      "（消防给水管道按 СП 10.13130 设计，非涂覆层要求）",
                      "—"))
        table.append(("涂层附着力",
                      "GB/T 5135.20 5.6：(1~3)级",
                      _chk_adh(coat_adh),
                      "（同上）",
                      "—"))

    L = ["## 三、尺寸与工艺关键指标对比（中国 vs 俄罗斯）",
         "| 指标 | 中国要求 | 中国判定 | 俄罗斯要求 | 俄罗斯判定 |",
         "|---|---|---|---|---|"]
    fails = []
    for name, cn_req, cn_v, ru_req, ru_v in table:
        L.append(f"| {name} | {cn_req} | {cn_v} | {ru_req} | {ru_v} |")
        for side, v in (("中国", cn_v), ("俄罗斯", ru_v)):
            if isinstance(v, str) and v.startswith("❌"):
                fails.append((name, side))
    L.append("> 注：外径/壁厚/长度/镀锌层/螺纹/液压/弯曲压扁按产品填值判定；弯曲度/单位长度重量需实测核对；"
             "镀锌层重量与厚度按 1 μm≈7.14 g/m² 换算，中国按重量(≥300 g/m²)、俄罗斯按厚度(≥30 μm)判定；"
             "CN 允许偏差按其标准公差、RU 按 ГОСТ 3262-75。")
    return {"table": "\n".join(L), "fails": fails}


def _bend_note(product: dict) -> str:
    od = product.get("od_mm")
    props = product.get("properties", {}) or {}
    bend = props.get("bending")      # pass / fail / 未做
    flat = props.get("flattening")   # pass / fail / 未做

    def _chk(v):
        if not v:
            return "未提供"
        return "✅ 满足" if str(v).lower() == "pass" else f"❌ 不合格（{v}；标准要求无裂纹）"

    L = ["## 一·八、弯曲 / 压扁试验（GB/T 3091-2025 6.6）"]
    if od is None:
        L.append("- （未提供公称外径，无法判定适用试验类型）")
        return "\n".join(L)
    test = "弯曲试验" if od <= 60.3 else "压扁试验"
    val = bend if od <= 60.3 else flat
    L.append(f"- 本规格（公称外径 {od} mm）适用：**{test}**（外径≤60.3mm 为弯曲、>60.3mm 为压扁）")
    L.append(f"- 中国 GB/T 3091-2025 要求：弯/压后**无裂纹** → 产品结果 {val or '未提供'} → {_chk(val)}")
    L.append("- 俄罗斯 ГОСТ 3262-75：**未规定弯曲/压扁试验**（VGP 管以水压试验 + 表面/尺寸检验 + σв/δ5 为主）")
    return "\n".join(L)


# 报告章节标题的俄文对照（lang=ru 时对最终文本做替换）
_RU_TITLES = {
    "## 一、产品概要": "## 1. Общие сведения о продукции",
    "## 二、牌号简要说明": "## 2. Краткая справка по маркам стали",
    "## 三、尺寸与工艺关键指标对比（中国 vs 俄罗斯）": "## 3. Ключевые размеры и технологические показатели (КНР vs РФ)",
    "## 四、中国法规合规评价": "## 4. Оценка соответствия стандартам КНР",
    "## 五、俄罗斯法规合规评价": "## 5. Оценка соответствия стандартам РФ",
    "## 六、合规项分类对比（中国 vs 俄罗斯）": "## 6. Сравнение по категориям показателей (КНР vs РФ)",
    "## 七、欧亚经济联盟技术法规评价": "## 7. Оценка по техрегламентам ЕАЭС",
    "## 八、集体结论（指数化）": "## 8. Итоговое заключение (индексация)",
    "## 九、EAC 认证注意事项与测试方法（咨询摘要）": "## 9. Сертификация EAC: примечания и методы испытаний",
}


def _mock_report(product: dict, r: dict, lang: str = 'zh') -> str:
    flag = {"pass": "✅", "fail": "❌", "na": "◻️"}

    def _nz(v):
        return '' if v is None else v

    def _d(v):
        return '—' if v is None else v

    def _pct(v):
        return '—' if v is None else f"{round(v * 100)}%" if v <= 1 else f"{round(v)}%"

    L = []
    L.append(f"# 《{_nz(r['product'].get('model')) or _nz(product.get('model'))}》中俄法规合规集体评价报告（初筛·Mock）")
    L.append("")
    L.append("> 说明：当前为 **无凭证/Mock 模式** 生成的示例报告，用于 MVP 演示与联调；"
             "接入 LLM（`--provider openai`）后由模型依据规则引擎判定 + 知识库条款生成正式报告。")
    L.append("")
    L.append("## 一、产品概要")
    L.append(f"- 型号：{_nz(r['product'].get('model'))}")
    L.append(f"- 类型：{_nz(r['product'].get('type'))}")
    L.append(f"- 材质（中/俄）：{_nz(r['product'].get('material_grade_cn'))} / {_nz(r['product'].get('material_grade_ru'))}")
    L.append(f"- 规格：DN {_nz(r['product'].get('dn_mm'))} mm，外径 {_nz(r['product'].get('od_mm'))} mm，壁厚 {_nz(r['product'].get('wall_thickness_mm'))} mm")
    L.append(f"- 工况：工作压力 {_nz(r['product'].get('working_pressure_mpa'))} MPa，介质 {_nz(r['product'].get('medium'))}")
    L.append(f"- 用途：{_nz(r['product'].get('intended_use'))}")
    L.append("")
    L.append("> ⚠️ **适用行业范围**：本报告**仅针对消防系统**（自动喷水灭火、室内外消火栓、消防给水等）用金属管材进行中俄法规合规评价；"
             "不适用于一般给排水、燃气、工业工艺、油气输送、锅炉等非消防管材。")
    L.append("")
    L.append(_grade_note(r['product'].get('material_grade_cn'), r['product'].get('material_grade_ru')))
    L.append("")
    _dt = _detail_table(r['product'], r)
    L.append(_dt["table"])

    def _color_summary(s):
        # 把"中国侧：合规/不合规"、"俄罗斯侧：合规/不合规"中的关键词着色
        import re
        s = re.sub(r"中国侧：合规", "中国侧：[[ok]]合规[[/ok]]", s)
        s = re.sub(r"中国侧：不合规", "中国侧：[[bad]]不合规[[/bad]]", s)
        s = re.sub(r"中国侧：评估不足，未定级", "中国侧：[[mask]]评估不足，未定级[[/mask]]", s)
        s = re.sub(r"俄罗斯侧：合规", "俄罗斯侧：[[ok]]合规[[/ok]]", s)
        s = re.sub(r"俄罗斯侧：不合规", "俄罗斯侧：[[bad]]不合规[[/bad]]", s)
        s = re.sub(r"俄罗斯侧：评估不足，未定级", "俄罗斯侧：[[mask]]评估不足，未定级[[/mask]]", s)
        return s

    def _side(title, key, skipped_key):
        L.append(f"## {title}")
        if not r[key]:
            L.append("（无适用标准或未命中）")
        for s in r[key]:
            L.append(f"### {s['standard']}　{s.get('name_zh', '')}")
            _nas = []
            for it in s["items"]:
                f = flag.get(it["verdict"], "?")
                # 无损检测为可选/按协议项，标注非强制，避免误读为必检指标
                _opt = "（可选/按协议，非强制）" if "无损" in (it.get("indicator_zh") or "") or "NDT" in (it.get("indicator_id") or "").upper() else ""
                _head = f"{it['indicator_zh']}{_opt}({it['indicator_id']})"
                _val = f"产品值={('未提供' if it['product_value'] is None else it['product_value'])} {it.get('unit') or ''}"
                if it["verdict"] == "na":
                    _nas.append(it)
                else:
                    L.append(f"- {f} {_head} "
                             f"{_val} "
                             f"→ **{it['verdict'].upper()}** | 条款: {it.get('clause') or '—'} | {it['detail']}")
            # 未评/需判定项：**合并为 1 个折叠块**（避免逐条 NA 刷屏），每项压成一行（原因 + 参考）
            if _nas:
                L.append(f":::na  ◻️ 未评 / 需判定项 {len(_nas)} 项（点开看每项原因与参考）")
                for it in _nas:
                    _opt = "（可选/非强制）" if "无损" in (it.get("indicator_zh") or "") or "NDT" in (it.get("indicator_id") or "").upper() else ""
                    _head = f"{it.get('indicator_zh')}{_opt}({it.get('indicator_id')})"
                    _val = "未提供" if it["product_value"] is None else it["product_value"]
                    _reason, _hint = _na_short(it.get("detail", ""))
                    L.append(f"   • **{_head}**　值={_val} {it.get('unit') or ''}　| 条款: {it.get('clause') or '—'}")
                    L.append(f"     ✋ {_reason}　💡 {_hint}")
                L.append(":::")
        for sid in r.get(skipped_key, []):
            L.append(f"- ⚠ 知识库缺失标准: {sid}")
        L.append("")

    _side("四、中国法规合规评价", "cn_results", "skipped_cn")
    _side("五、俄罗斯法规合规评价", "ru_results", "skipped_ru")

    # C：分类对比表（同一套类别，两侧对齐）
    L.append("## 六、合规项分类对比（中国 vs 俄罗斯）")
    L.append("> 说明：按同一套类别逐类对比，避免因细分粒度不同导致项数悬殊的误读。√=通过 ×=未通过 ◻=未适用/需判定")
    L.append("")
    L.append("| 类别 | 中国（项/√/×/◻） | 俄罗斯（项/√/×/◻） |")
    L.append("|---|---|---|")
    st = _cat_table(r)
    for g, d in st.items():
        c = d["cn"]
        ru = d["ru"]
        cn_cell = f"{c[0]} / {c[1]} / {c[2]} / {c[3]}" + ("　（中标准未规定）" if c[0] == 0 else "")
        ru_cell = f"{ru[0]} / {ru[1]} / {ru[2]} / {ru[3]}" + ("　（俄标准未规定）" if ru[0] == 0 else "")
        L.append(f"| {g} | {cn_cell} | {ru_cell} |")
    if not st:
        L.append("| （无命中项） | — | — |")
    L.append("")

    tr = r["tr_cu_032"]
    L.append("## 七、欧亚经济联盟技术法规评价")
    L.append(f"### ТР ТС 032/2013（承压设备）")
    L.append(f"- P×DN = {_d(tr.get('pdn'))} МПа·мм（压力×公称通径）| 适用: **{_d(tr.get('in_scope'))}** | 表{_d(tr.get('table'))} | "
             f"Категория（类别）{_d(tr.get('category'))} | {_d(tr.get('route'))} | {_d(tr.get('note'))}")
    L.append(f"### ТР ЕАЭС 043/2017（消防产品）")
    tr43 = r["tr_eaeu_043"]
    L.append(f"- 适用: **{tr43.get('applies')}** | 路径: {tr43.get('route') or '—'}")
    L.append(f"- {tr43.get('note', '')}")
    L.append("")

    idx = r["index"]
    L.append("## 八、集体结论（指数化）")

    def _grade_c(g):   # A/B → 绿色；C/D → 红色；NA → 灰
        if g in ("A", "B"):
            return f"[[ok]]{g}（良）[[/ok]]"
        if g in ("C", "D"):
            return f"[[bad]]{g}（差）[[/bad]]"
        return f"[[mask]]未定级[[/mask]]"

    def _vn_c(v):      # 合规 → 绿；不合规 → 红；待定 → 灰
        if v == "合规":
            return "[[ok]]合规[[/ok]]"
        if v == "不合规":
            return "[[bad]]不合规[[/bad]]"
        return "[[mask]]评估不足，未能定论[[/mask]]"

    def _idx_line(si, label):
        g = _d(si["grade"])
        tot = si["passed"] + si["failed"]
        res = "全部通过" if si["failed"] == 0 else f"{si['failed']} 项未通过"
        if g == "NA":
            return (f"- {label}合规度 CI = {_d(si['ci'])}，评级 {_grade_c(g)}"
                    f"（覆盖度 {_pct(si.get('coverage'))} 不足、未评 {si.get('na', 0)} 项偏多，故不给 A~D；已评 {tot} 项{res}）")
        return (f"- {label}合规度 CI = {_d(si['ci'])}，评级 {_grade_c(g)}"
                f"（可评 {tot} 项{res}；未评/需判定 {si.get('na', 0)} 项；覆盖度 {_pct(si.get('coverage'))}）")

    L.append(_idx_line(idx['cn'], "中国"))
    L.append(_idx_line(idx['ru'], "俄罗斯"))
    # 集体结论：把"合规/不合规"着色
    L.append(f"- **集体结论**：{_color_summary(r['overall']['summary'])}")
    # 中俄牌号不对应 → 在集体结论处以橙黄色警示（等效性需核实）
    _gcn = _nz(product.get("material_grade_cn"))
    _gru = _nz(product.get("material_grade_ru"))
    _mism = _grade_mismatch(product.get("material_grade_cn"), product.get("material_grade_ru"))
    if _mism:
        L.append(f"- ⚠ **牌号对应性**：中牌号 **{_gcn}** 通常近似对应俄 **{_mism}**，但你填写的俄牌号是 **{_gru}**，"
                 f"[[warn]]两者不对应，等效性需认证机构核实[[/warn]]。")
    # 覆盖度分级说明：按未评/需判定占比给出评估充分性提示
    def _cov_label(c):
        if c is None:
            return "（无覆盖度）"
        if c >= 0.95:
            return "评估较充分"
        if c >= 0.80:
            return "评估一般"
        if c >= 0.50:
            return "评估不足，建议补检"
        return "评估不足，未评项占多数"
    _low_cov = [("中国", idx['cn'].get('coverage')), ("俄罗斯", idx['ru'].get('coverage'))]
    _low = [(n, c) for n, c in _low_cov if c is not None and c < 1.0]
    if _low:
        _txt = "；".join(f"{n}覆盖度 {_pct(c)}（{_cov_label(c)}）" for n, c in _low)
        L.append(f"- 📌 **评估说明**：{_txt} —— 另有未评/需判定项（如尺寸公差、无损检测(可选)、按协议项目等），"
                 f"故以上评级为该侧[[mask]]可评项范围内的合规度[[/mask]]，[[mask]]不构成对未评项的豁免[[/mask]]。")
    # 汇总"未达标项"：① §三 尺寸/工艺表 ❌；② §四/§五 各标准被判 fail 的关键指标。
    # 这样把"伸长率/力学性能不达标"等也带进结论，避免只有表里 ❌ 才提示。
    def _side_fails(key, label):
        out = []
        for s in r.get(key, []):
            for it in s.get("items", []):
                if it.get("verdict") == "fail":
                    out.append((it.get("indicator_zh") or it.get("indicator_id"), label))
        return out

    _fails = list(_dt.get("fails", []))
    _fails += _side_fails("cn_results", "中国")
    _fails += _side_fails("ru_results", "俄罗斯")
    # 去重（同指标同侧）
    seen, uniq = set(), []
    for n, side in _fails:
        if (n, side) not in seen:
            seen.add((n, side))
            uniq.append((n, side))
    if uniq:
        _ftxt = "、".join(f"{n}（{side}）" for n, side in uniq[:6]) + (" 等" if len(uniq) > 6 else "")
        L.append(f"- ⚠ **存在未达标项**：{_ftxt} → **不能判定为完全合规**，需整改/复核（详见「三」尺寸表与「四/五」各标准判定）。")
    else:
        L.append("- ✅ 关键尺寸/工艺指标均满足对应标准要求（详见「三」）。")
    # 边缘不达标（接近临界，差距≤5%）：橙黄提示，提醒优先复核，避免出口检测时再暴露
    _edges = []
    for key, side in (("cn_results", "中国"), ("ru_results", "俄罗斯")):
        for s in r.get(key, []):
            for it in s.get("items", []):
                if it.get("verdict") == "fail":
                    gap = _edge_gap(it.get("detail", ""))
                    if gap is not None and gap <= 0.05:
                        _edges.append((it.get("indicator_zh") or it.get("indicator_id"), side, it.get("detail", "")))
    if _edges:
        _etxt = "；".join(f"{n}（{side}）「{_edge_short(d)}」" for n, side, d in _edges[:4])
        if len(_edges) > 4:
            _etxt += " 等"
        L.append(f"- ⚠ [[warn]]边缘不达标（接近临界）[[/warn]]：{_etxt} → 差距很小但确实未达标准，建议优先复核（详见「四/五」各标准判定）。")
    L.append("")
    L.append("## 九、EAC 认证注意事项与测试方法（咨询摘要）")
    L.append("- **适用法规判定**：典型消防给水管（水、Группа 2（第2组介质）液体、DN≤200 或 P×DN≤500 МПа·мм）**不属 ТР ТС 032/2013（承压设备法规）** 范围；但作为消防系统管道须按 **ТР ЕАЭС 043/2017（消防器材法规）强制认证**。")
    L.append("- **持证主体**：EAC 证书须由**欧亚经济联盟境内主体**持有——中国企业不能直接持证，需通过俄/吉/哈等境内委托方办理；近年证书范围（область распространения（证书范围））收窄至**俄罗斯本地**。")
    L.append("- **测试方法**：消防自动灭火系统元件做耐火性（огнестойкость（耐火性））、火灾工况下承压与密封、动作可靠性/喷水强度；管材材料性能按 ГОСТ（3262-75/10705-80/8731-87 等）验收；检测报告须由 **ГОСТ ISO/IEC 17025-2019（实验室能力认可）认可**实验室出具。")
    L.append("- **费用/周期（调研时点，以认证机构最新报价为准）**：俄认证机构约 **51 万卢布 / 3–4 个月**；吉尔吉斯约 **42 万卢布 / 2–3 个月**；专家到厂费另计。")
    L.append("")
    L.append("> ⚠️ 本结果为规则引擎初筛，最终结论须由认证机构/认可实验室（ГОСТ ISO/IEC 17025-2019）确认；"
             "不构成法律意见或认证承诺。")
    L.append(">")
    L.append("> ⚖️ **法律风险提示**：① 知识库部分数值标注 `[待核]`，须以标准**官方正式文本**为准；"
             "② GB/ГОСТ 标准文本**受版权保护**，本报告仅引用条款编号与要点，不得替代标准原文；"
             "③ 商业宣传须遵守《广告法》，**不得使用绝对化用语或承诺认证通过**；"
             "④ 经营性服务须具备主体资质并依法纳税，服务器所在地不影响法律责任；"
             "⑤ 收集处理用户信息须遵守《个人信息保护法》。完整条款见 `/terms`（免责声明与法律风险提示）。")
    _out = "\n".join(L)
    if lang == "ru":
        for _zh, _ru in _RU_TITLES.items():
            _out = _out.replace(_zh, _ru)
        _out = _out.replace("中俄法规合规集体评价报告（初筛·Mock）", "Отчёт о соответствии (КНР/РФ, предварительный · mock)")
        _out = _out.replace("> 说明：当前为 **无凭证/Mock 模式** 生成的示例报告",
                            "> Примечание: отчёт сформирован в **режиме mock (без ключа LLM)**")
    return _out


# ---------------------------------------------------------------------------
# 4. 主入口
# ---------------------------------------------------------------------------
def generate_report(product: dict, result: dict, cfg: dict, lang: str = "zh") -> dict:
    """返回 {"provider": ..., "report": "...", "engine_result": ...}"""
    if cfg["provider"] == "openai":
        system, user = build_prompt(product, result)
        report = _chat_completion(system, user, cfg)
    elif lang == "ru":
        # 俄语界面：整篇报告用俄文渲染（report_ru 只负责 lang=ru，不触碰中文路径）
        import report_ru
        report = report_ru.report_ru(product, result)
    else:
        report = _mock_report(product, result, lang)
    return {"provider": cfg["provider"], "report": report, "engine_result": result}


def main():
    ap = argparse.ArgumentParser(description="LLM 集体评价报告生成")
    ap.add_argument("input", help="产品参数 JSON 文件路径（含 product 包装）")
    ap.add_argument("--provider", choices=["mock", "openai"], default=None,
                    help="覆盖环境变量 LLM_PROVIDER")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON（含 report + engine_result）")
    args = ap.parse_args()

    cfg = get_config()
    if args.provider:
        cfg["provider"] = args.provider

    raw = json.loads(Path(args.input).resolve().read_text(encoding="utf-8"))
    product = raw.get("product", raw)
    result = E.evaluate(product)
    out = generate_report(product, result, cfg)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(out["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
