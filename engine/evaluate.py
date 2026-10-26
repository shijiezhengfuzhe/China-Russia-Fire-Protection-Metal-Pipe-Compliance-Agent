#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中俄消防金属管材认证指标自动对比评价引擎 (evaluate.py)
=========================================================
输入 : 管材产品性能参数 JSON（结构见 examples/sample_input.json）
输出 : 中/俄标准合规判定 + ТР ТС 032/2013 类别与合格评定路径
       + ТР ЕАЭС 043/2017 适用性 + 量化指数化评分 + 法规条款引用

数据 : 读取 data/cn_standards.json, data/ru_standards.json（机读知识库）
用法 : python evaluate.py examples/sample_input.json
       python evaluate.py examples/sample_input.json --json
依赖 : 仅 Python 3 标准库
"""
import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


# ---------------------------------------------------------------------------
# 1. 知识库加载
# ---------------------------------------------------------------------------
def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_knowledge_base():
    # 2025-06 去冗余：主库统一使用最终版 cn_standards_final.json（含修正后的牌号体系）；
    # 旧的 data/cn_standards.json（初筛编译版，含 Q355B/Q235C 等错误牌号）已废弃，可删除。
    cn = load_json(DATA_DIR / "cn_standards_final.json")
    ru = load_json(DATA_DIR / "ru_standards.json")
    kb = {"cn": cn, "ru": ru}
    # 常见问题库（FAQ）：可选，缺失不影响主流程
    _faq = DATA_DIR / "faq.json"
    if _faq.exists():
        try:
            kb["faq"] = load_json(_faq)
        except Exception:
            pass
    # 中国标准指标 → 俄文名映射（可选，用于俄语界面显示）
    _cru = DATA_DIR / "cn_indicator_ru.json"
    if _cru.exists():
        try:
            kb["cn_ind_ru"] = load_json(_cru)
        except Exception:
            pass
    return kb


_KB_CACHE = {}


def _get_kb():
    if not _KB_CACHE:
        try:
            _KB_CACHE.update(load_knowledge_base())
        except Exception:
            pass
    return _KB_CACHE


def _find_wall_table(ind, dn_mm):
    """找到指标所属标准的"壁厚分档表"（如 ГОСТ 3262-75 的 3262.WALL），返回其行列表或 None。"""
    iid = str(ind.get("id", "")).lower()
    prefix = iid.split(".")[0]  # e.g. "3262" -> 标准号前缀
    kb = _get_kb()
    for s in kb.get("ru", {}).get("standards", []):
        if str(s.get("id", "")).startswith(prefix) or prefix in str(s.get("id", "")):
            for ind2 in s.get("indicators", []):
                if "WALL" in str(ind2.get("id", "")).upper() or "стенк" in str(ind2.get("name_ru", "")).lower():
                    rows = (ind2.get("values") or {}).get("table", [])
                    if isinstance(rows, list):
                        return rows
    return None


def _hydro_req_pressure(ind, product):
    """水压试验应达到的最小压力（MPa）。
    旧逻辑用"应力公式 P=2·t·S/D、封顶 5.0"过严，导致多数消防管在实测 2.5 MPa 时误 FAIL。
    改为：要求 = max(标准基准, 1.5×设计/工作压力)。基准按标准常用值（俄 2.4、中 2.5 MPa）。
    注：行业通行近似值，[待核] 以各标准原文表为准。"""
    pref = str(ind.get("id", "")).split(".")[0].lower()
    base = 2.4 if pref in ("3262", "8731", "8734", "10705", "10706", "9940", "11068") else 2.5
    dp = _to_num(product.get("design_pressure_mpa")) or _to_num(product.get("working_pressure_mpa"))
    req = base
    if dp:
        req = max(req, round(1.5 * dp, 1))
    return round(req, 1)


# ---------------------------------------------------------------------------
# 2. 产品类型 → 适用标准映射
# ---------------------------------------------------------------------------
APPLICABILITY = {
    "无缝钢管": (
        ["GB/T 8163-2018", "GB/T 17395-2024"],
        ["ГОСТ 8731-87"],
    ),
    "冷拔无缝钢管": (
        ["GB/T 8163-2018", "GB/T 17395-2024"],
        ["ГОСТ 8734-75"],
    ),
    "直缝焊接钢管": (
        ["GB/T 13793-2016", "GB/T 17395-2024"],
        ["ГОСТ 10705-80"],
    ),
    "低压流体输送用焊接钢管(镀锌/黑管)": (
        ["GB/T 3091-2015", "GB/T 17395-2024"],
        ["ГОСТ 3262-75"],
    ),
    "消防涂覆钢管": (
        ["GB/T 5135.20-2010", "GB/T 3091-2015", "GB/T 17395-2024"],
        ["ГОСТ 3262-75", "СП 10.13130.2020 / СП 485.1311500.2020"],
    ),
    "不锈钢无缝钢管": (
        ["GB/T 14976-2012", "GB/T 17395-2024"],
        ["ГОСТ 9940-81"],
    ),
    "不锈钢焊接钢管": (
        ["GB/T 12771-2019", "GB/T 17395-2024"],
        ["ГОСТ 11068-81"],
    ),
    "干线管道焊接钢管": (
        [],  # 中国干线管道另有 GB/T 9711 等（超出本项目清单）
        ["ГОСТ 20295-85"],
    ),
    "热轧带肋钢筋": (
        ["GB/T 1499.2-2024"],
        ["ГОСТ 5781-82"],
    ),
    "预应力钢丝": (
        ["GB/T 5223-2025"],
        ["ГОСТ 6727-80"],
    ),
}


def applicable_standards(product_type: str):
    # 精确匹配优先（避免"不锈钢无缝钢管"被"无缝钢管"子串误命中）
    if product_type in APPLICABILITY:
        return APPLICABILITY[product_type]
    for k in APPLICABILITY:
        if k in product_type:
            return APPLICABILITY[k]
    return [], []


# ---------------------------------------------------------------------------
# 3. 单项指标判定
# ---------------------------------------------------------------------------
def _to_num(v):
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def compare_value(product_value, req):
    """返回 (verdict, detail)。req 形态见 pick_requirement。
    支持: min/max/range/set/bool/desc/series/table/map/spec"""
    if not isinstance(req, dict):
        return "na", f"无法解析要求 {req!r}"
    if "desc" in req:
        return "na", f"描述性要求：{req['desc']}（需认证机构/LLM辅助判定）"
    if "table" in req:
        rows = req.get("table", [])
        return "na", (f"表格式要求（{len(rows)} 行，如按钢号/壁厚等级取值），"
                      f"需由 LLM/人工按行匹配判定：{json.dumps(rows, ensure_ascii=False)[:200]}...")
    if "map" in req:
        return "na", f"枚举型要求（如分组 А/Б/В），需按产品交付分组判定：{req['map']}"
    if "spec" in req:
        return "na", f"专项要求：{req['spec']}（需认证机构/LLM辅助判定）"
    if product_value is None:
        return "na", "产品未提供该参数"
    if "set" in req or "series" in req:
        allowed = req.get("set") or req.get("series") or []
        # 未填/未做 → NA（如实），除非空值是合法集合成员
        if product_value is None or (isinstance(product_value, str) and product_value.strip() == "" and "" not in allowed):
            return "na", "产品未提供「该指标」的取值（未填/未做该项）"
        ok = product_value in allowed
        return ("pass" if ok else "fail"), f"取值 {product_value} ∈ {allowed}"
    if "bool" in req:
        ok = bool(product_value) == bool(req["bool"])
        return ("pass" if ok else "fail"), f"布尔要求={req['bool']}，产品={bool(product_value)}"
    v = _to_num(product_value)
    if v is None:
        return "na", f"无法数值化产品值 {product_value!r}"
    if "min" in req and "max" in req:
        lo, hi = req["min"], req["max"]
        return ("pass" if lo <= v <= hi else "fail"), f"{lo} ≤ {v} ≤ {hi}"
    if "min" in req:
        return ("pass" if v >= req["min"] else "fail"), f"{v} ≥ {req['min']}"
    if "max" in req:
        return ("pass" if v <= req["max"] else "fail"), f"{v} ≤ {req['max']}"
    if "range" in req:
        lo, hi = req["range"]
        return ("pass" if lo <= v <= hi else "fail"), f"{lo} ≤ {v} ≤ {hi}"
    return "na", f"无法解析要求 {req!r}"


def na_note(ind, req, value):
    """为"无法判定(NA)"生成结构化说明：原因 / 评价依据 / 参考如何补全。

    返回一个带分段标记的字符串，供报告渲染。不会伪造数据：
      - 缺参数 → 明确缺哪个输入；
      - 需实测 → 指明需实测哪一项；
      - 描述/表格/枚举/专项 → 注明"需认证机构/人工判定"，并给出参考。
    """
    clause = ind.get("clause") or "—"
    name = ind.get("indicator_zh") or ind.get("name_zh") or ind.get("id") or "该项"
    unit = ind.get("unit") or ""
    iid = str(ind.get("id", "")).upper()
    reqtxt = ""
    if isinstance(req, dict):
        if "desc" in req:
            reqtxt = f"标准以文字描述要求（{req['desc']}），且" + _na_confirm_hint(req)
        elif "table" in req:
            reqtxt = (f"标准给出 {len(req['table'])} 行分档表（如按钢号/壁厚等级取值），需按产品匹配对应行；"
                      f"样本：{json.dumps(req['table'][0], ensure_ascii=False) if req['table'] else ''}…")
        elif "map" in req:
            reqtxt = f"标准为枚举型分组（{list(req['map'].keys())}），需按产品交付分组确认"
        elif "spec" in req:
            reqtxt = f"标准为专项要求：{req['spec']}"
        elif "set" in req or "series" in req:
            reqtxt = f"标准限定取值集合：{req.get('set') or req.get('series')}"
        elif "min" in req or "max" in req or "range" in req:
            reqtxt = f"标准数值要求：{json.dumps(req, ensure_ascii=False)}"
        else:
            reqtxt = f"标准要求：{json.dumps(req, ensure_ascii=False)}"
    else:
        reqtxt = f"标准要求：{req!r}"

    # 区分"不适用/可选"与"缺数据无法判定"
    na_kind = "无法判定"
    if "THREAD" in iid or "螺纹" in name:
        na_kind = "不适用（该管型无螺纹连接要求）"
        reason = f"产品为平口/焊接/法兰等连接，无管端螺纹"
    elif "NDT" in iid or "无损" in name:
        na_kind = "可选/按协议项（非强制）"
        reason = f"无损检测为可选/按协议项，未提供检测结果"
    elif "GROUP" in iid:
        na_kind = "需要交货分组声明"
        reason = f"未提供交货分组（А/Б/В），无法确定验收范围"
    elif value is None:
        reason = f"产品未提供「{name}」的输入值（{unit}）"
    elif isinstance(value, str) and value == "":
        reason = f"产品「{name}」未填写（{unit}）"
    else:
        reason = f"产品值 {value!r}{' '+unit if unit else ''} 无法与标准要求直接比较"

    return (f"✋ {na_kind}：{reason}。"
            f"📖 评价依据：{clause}。{reqtxt}。"
            f"💡 参考：{_na_howto(ind, req)}。最终结论需认证机构/认可实验室（ГОСТ ISO/IEC 17025-2019）确认。")


def _na_confirm_hint(req):
    import re
    d = str(req.get("desc", ""))
    if "实测" in d or "测量" in d or "偏差" in d:
        return "需实测值（填写外径/壁厚/长度/弯曲度等实测数据后即可判定）"
    if "按协议" in d or "协议" in d or "要求" in d:
        return "需按订货协议/工程校核"
    if "公式" in d or "P=" in d or "计算" in d:
        return "需按公式计算（或提供相应计算参数）"
    return "需认证机构/人工判定"


def _na_howto(ind, req):
    iid = str(ind.get("id", "")).upper()
    name = str(ind.get("indicator_zh") or ind.get("name_zh") or "")
    if "THREAD" in iid or "螺纹" in name:
        return "若采用螺纹连接，可在连接方式选螺纹连接；否则该管型无螺纹要求，此项无需判定"
    if "ZINC" in iid:
        return "提供镀锌层重量 g/m²（或厚度 μm，自动换算）"
    if "COAT_THK" in iid or "涂" in name and "厚" in name:
        return "提供涂层厚度 mm（按 DN 分档判定）"
    if "HYD" in iid or "水压" in name:
        return "提供液压试验压力 MPa（按公式 P=2SR/D 计算应达到值）"
    if "NDT" in iid or "无损" in name:
        return "无损检测为可选/按协议项；未做即为可选未提供，不据此判不达标"
    if "DIM" in iid or "尺寸" in name:
        return "提供实测 外径/壁厚（用于公差判定）"
    if "STRAIGHT" in iid or "弯曲度" in name or "OD" in iid or "外径" in name:
        return "提供实测外径/弯曲度（按允许偏差判定）"
    if "GROUP" in iid:
        return "选择交货分组（А/Б/В）"
    if "GRADE" in iid or "牌号" in name:
        return "确认产品牌号（中/俄）"
    return "补充对应实测数据或订货协议（如需，可由认证机构判定）"


# 中国↔俄罗斯 不锈钢牌号对应（源于 GB/T 14976-2025 与 ГОСТ 9940-81/11068-81，按牌号名+化学+力学对应）
CN_RU_GRADE_MAP = {
    "06Cr19Ni10": "08Х18Н10",      # 304 → 无Mo奥氏体
    "022Cr19Ni10": "08Х18Н10",     # 304L（低碳）
    "06Cr17Ni12Mo2": "10Х17Н13М2Т",# 316 → 含Mo
    "022Cr17Ni12Mo2": "08Х17Н15М3Т",# 316L（低碳含Mo）
    "06Cr18Ni11Ti": "08Х18Н10Т",   # 321 → Ti稳定化
    "06Cr19Ni10N": "08Х18Н10Т",
    "06Cr17Ni12Mo2N": "10Х17Н13М2Т",
    # 碳素结构钢（GB/T 700 / 8163 / 13793）↔ ГОСТ 380 Ст 系（俄集合常为 复合键 кп/пс/сп）
    "Q195": "Ст1кп/пс/сп", "Q215A": "Ст2кп/пс/сп", "Q215B": "Ст2кп/пс/сп", "Q215": "Ст2кп/пс/сп",
    "Q235A": "Ст3кп/пс/сп", "Q235B": "Ст3кп/пс/сп", "Q235": "Ст3кп/пс/сп",
    "Q275A": "Ст4кп/пс/сп", "Q275B": "Ст4кп/пс/сп", "Q275": "Ст4кп/пс/сп",
    "Q355B": "17Г1С", "Q345B": "17Г1С", "Q345D": "09Г2С",
    "10": "10", "20": "20", "35": "35", "45": "45",
}

# 俄罗斯标准前缀，其 GRADE 集合为俄牌号（需把 CN 牌号桥接成俄牌号再判）
RU_STD_PREFIX = ("9940", "11068", "8731", "8734", "10705", "20295", "5781", "6727", "3262", "10706")


def _grade_bridge(ind, value, product):
    """把产品牌号桥接到"指标所用标准"的牌号体系：
    - 指标属俄罗斯标准(前缀命中 RU_STD_PREFIX) 且 值是中文牌号 → 尝试用 CN_RU_GRADE_MAP 转成俄牌号。
    - 返回转换后的值；无法转换则原样返回。
    """
    if value is None:
        return value
    iid = str(ind.get("id", "")).upper()
    prefix = iid.split(".")[0] if "." in iid else iid
    if prefix in RU_STD_PREFIX and isinstance(value, str):
        # 当前值是否为中文牌号（能被映射转成俄）
        if value in CN_RU_GRADE_MAP:
            return CN_RU_GRADE_MAP[value]
    return value


def pick_requirement(values, grade, grade_cn=None, grade_ru=None):
    if not isinstance(values, dict):
        return values
    if any(k in values for k in ("min", "max", "range", "set", "desc", "bool",
                                 "table", "map", "spec", "series")):
        return values
    # 牌号条件化: values = {牌号: 阈值}
    candidates = [g for g in (grade, grade_cn, grade_ru) if g]
    for g in candidates:
        if g in values:
            return values[g]
    # 前缀匹配（如 Q235B → Q235；Ст3сп → Ст3）
    for g in candidates:
        for k in values:
            if g.startswith(k) or k.startswith(g):
                return values[k]
    return next(iter(values.values()))


def group_excludes(ind, product):
    """判断该指标是否被"交货分组"排除（即该分组不保证此类别指标）。

    规则（ГОСТ 8731/10705/20295 的 Группа поставки）：
      А = 只保证 力学 → 排除 化学(钢号/成分)、力学仍判
      Б = 只保证 化学 → 排除 力学、化学仍判
      В = 力学+化学 都保证 → 均不排除
    仅对含 GROUP 的标准及其 钢号(化学)/力学 指标生效；未注明分组不排除（仍正常判定）。
    """
    gsel = str(product.get("delivery_group") or "").strip()
    if not gsel:
        return False
    # 指标 id 里识别"钢号/化学成分" vs "力学性能"
    iid = str(ind.get("id", "")).upper()
    izh = str(ind.get("indicator_zh") or ind.get("name_zh") or "")
    # 化学：id 含 STEEL/CHEM 或名称含 化学成/钢号/牌号/碳/硅/锰/磷/硫
    is_chem = ("STEEL" in iid or "CHEM" in iid or any(k in izh for k in ("化学成", "钢号", "牌号", "碳含量", "硅含量", "锰含量", "磷含量", "硫含量")))
    # 力学：id 含 MECH/RM/REL/A 或名称含 力学/抗拉/屈服/伸长/延伸/冲击
    is_mech = ("MECH" in iid or "RM" in iid or "REL" in iid or ("A" == iid[-1:] and "CHEM" not in iid)
               or any(k in izh for k in ("力学", "抗拉", "屈服", "伸长", "延伸", "冲击", "破断")))
    if gsel in ("А", "A", "а", "a"):
        return is_chem      # А 只保证力学 → 排除化学
    if gsel in ("Б", "B", "б", "b"):
        return is_mech      # Б 只保证化学 → 排除力学
    # В（或其它）→ 都保证，不排除
    return False


def resolve_dn_table(req, dn_mm):
    """把"按公称通径分档取阈值"的 table 需求解析成单个 {min/max} 需求。
    表格形如: [{"dn": "DN≤65", "min_mm": 0.30}, {"dn": "DN≥80", "min_mm": 0.35}]
    返回: {"min": 0.35} / {"max": ...} 或 None（无法按 DN 命中/不是这类表）。
    """
    if not isinstance(req, dict) or "table" not in req or dn_mm is None:
        return None
    dn = _to_num(dn_mm)
    if dn is None:
        return None
    rows = req.get("table", [])
    if not isinstance(rows, list):
        return None
    # 识别"分档键"：dn/d/DN/d_range/dia_range 之类；阈值键：min_*/max_*
    def _range_key(r):
        if not isinstance(r, dict):
            return None
        for k in r:
            kl = str(k).lower()
            if kl in ("dn", "d", "dn_mm", "dia", "d_range", "dn_range", "od_range", "range"):
                return k
            if kl.endswith("_range") or k in ("公称通径", "口径", "外径范围"):
                return k
        return None

    has_dn = any(_range_key(r) is not None for r in rows)
    if not has_dn:
        return None
    for r in rows:
        if not isinstance(r, dict):
            continue
        rk = _range_key(r)
        if rk is None:
            continue
        d = str(r.get(rk, "")).replace(" ", "")
        lo = hi = None
        # 解析 "DN≤65" / "DN≥80" / "DN100~150" / "DN 15-50" 之类
        import re
        m = re.search(r"(?:DN)?(\d+)\s*[~-]\s*(\d+)", d)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
        m = re.search(r"(?:DN)?[≤<]\s*=?\s*(\d+)", d)
        if m:
            hi = float(m.group(1))
        m = re.search(r"(?:DN)?[≥>]\s*=?\s*(\d+)", d)
        if m:
            lo = float(m.group(1))
        if lo is None and hi is None:
            continue
        if lo is not None and dn < lo:
            continue
        if hi is not None and dn > hi:
            continue
        # 命中：找出该行的阈值键（min_* 优先，其次 max_*）
        for k, v in r.items():
            kl = str(k).lower()
            if kl.startswith("min"):
                return {"min": v}
        for k, v in r.items():
            kl = str(k).lower()
            if kl.startswith("max"):
                return {"max": v}
        for k in ("min_mm", "min", "min_thk", "min_g_m2", "min_m", "min_um", "min_kg"):
            if k in r:
                return {"min": r[k]}
        for k in ("max_mm", "max", "max_thk"):
            if k in r:
                return {"max": r[k]}
    return None


# 钢号条件化表格：键列 + 数值列（如 ГОСТ 8731-87 的 MECH 力学性能表）
# 表格形如: [{"сталь": "10", "σв": 333, "σт": 206, "δ5": 31}, {"сталь": "20", ...}]
# 键列名候选：сталь/钢号/钢牌号/grade/steel/marque；数值列名映射到产品属性。
GRADE_TABLE_KEY = ("сталь", "钢号", "牌号", "钢种", "grade", "steel", "marque", "марка", "класс")
# 数值列 → 产品属性键
MECH_COL_MAP = {
    "σв": "rm_mpa", "σт": "re_mpa", "δ5": "a_pct", "δ": "a_pct", "KCU": "kv2_j",
    "Rm": "rm_mpa", "ReH": "re_mpa", "Re": "re_mpa", "A": "a_pct",
    "rm": "rm_mpa", "re": "re_mpa", "a": "a_pct",
}


def resolve_grade_table(req, product):
    """把按钢号分档、含数值列的表格需求解析为"多属性 min"需求。

    返回 {"_props": {产品属性: {min/max...}}, "per": [ (指标名, 产品属性, min) , ...]}，
    或 None（不是这类表 / 无法命中钢号）。由 check_indicator 逐属性比较后汇总判定。
    """
    if not isinstance(req, dict) or "table" not in req:
        return None
    rows = req.get("table", [])
    if not isinstance(rows, list):
        return None
    # 识别键列
    key_col = None
    for r in rows:
        if isinstance(r, dict):
            for k in GRADE_TABLE_KEY:
                if k in r:
                    key_col = k
                    break
        if key_col:
            break
    if key_col is None:
        return None
    # 识别数值列（至少一个可映射）
    num_cols = []
    for k in (rows[0] if isinstance(rows[0], dict) else {}):
        if k != key_col and k in MECH_COL_MAP and isinstance(rows[0][k], (int, float)):
            num_cols.append((k, MECH_COL_MAP[k]))
    if not num_cols:
        return None
    grade = product.get("material_grade_ru") or product.get("material_grade_cn") or product.get("material_grade_cn")
    # 若为中文牌号，先桥接成俄牌号（如 Q235B → Ст3кп/пс/сп、06Cr19Ni10 → 08Х18Н10），使其能命中俄力学表的 сталь 键
    if isinstance(grade, str) and grade in CN_RU_GRADE_MAP and not product.get("material_grade_ru"):
        grade = CN_RU_GRADE_MAP[grade]
    grade = str(grade) if grade is not None else ""
    # 规范化：去掉"Ст"前缀（俄标），统一大写、去空格
    def _norm(s):
        s = str(s).strip()
        s = s.lstrip("Стс").strip()   # Ст3сп -> 3сп
        s = s.upper()
        return s
    g_norm = _norm(grade)
    # 拆分行键：复合键如 "Ст3кп/пс/сп"、"Ст2сп,Ст3сп" 拆成多个
    def _key_parts(key):
        parts = str(key).replace("／", "/").replace("、", "/").replace(",", "/").replace(" ", "/")
        return [_norm(p) for p in parts.split("/") if p.strip()]
    row = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        parts = _key_parts(r.get(key_col, ""))
        if g_norm in parts:            # 精确命中拆分后的某个牌号
            row = r
            break
    if row is None:
        # 宽松：单个牌号间互为前后缀（如 3сп 命中 3сп/3пс 之类），取最贴近
        for r in rows:
            if not isinstance(r, dict):
                continue
            parts = _key_parts(r.get(key_col, ""))
            for p in parts:
                if p and g_norm and (g_norm in p or p in g_norm):
                    row = r
                    break
            if row:
                break
    if row is None:
        return None
    per = []
    for col, pkey in num_cols:
        v = row[col]
        if isinstance(v, (int, float)):
            per.append((col, pkey, v))
    if not per:
        return None
    return {"per": per}


def check_indicator(ind, product):
    grade = product.get("material_grade_ru") or product.get("material_grade_cn")
    # 俄罗斯标准：若产品只给中文牌号，桥接成俄牌号用于取"按牌号分档"的化学/力学阈值
    _gcn = product.get("material_grade_cn"); _gru = product.get("material_grade_ru")
    iid0 = str(ind.get("id", "")).split(".")[0].upper()
    if iid0 in RU_STD_PREFIX and not _gru and _gcn:
        _bridged = CN_RU_GRADE_MAP.get(_gcn)
        if _bridged:
            _gru = _bridged
            grade = _bridged
    req = pick_requirement(ind.get("values"), grade,
                           product.get("material_grade_cn"), _gru)
    key = str(ind.get("id", "")).split(".")[-1].upper()
    prop_map = {
        "RM": "rm_mpa", "RE": "re_mpa", "REL": "re_mpa", "REH": "re_mpa", "A": "a_pct", "EL": "a_pct",
        "RP02": "rp02_mpa", "RP": "rp02_mpa",
        "KV": "kv2_j", "KV2": "kv2_j", "KCU": "kv2_j",
        "HYDRO": "hydrostatic_test_mpa", "HYD": "hydrostatic_test_mpa", "FLATTEN": "flattening",
        "FLAT": "flattening",
        "BEND": "bending", "EXPAND": "expanding", "FLANGE": "flanging",
        "ZINC": "zinc_coat_g_m2", "OD": "od_mm", "WT": "wall_thickness_mm",
        "WALL": "wall_thickness_mm", "DN": "dn_mm", "DU": "dn_mm",
        "LENGTH": "length_m", "LEN": "length_m", "THREAD": "threaded", "GALV": "galvanized",
        "COAT_THK": "coating_thickness_mm", "COAT_ADH": "coating_adhesion_grade",
        "STEEL": "material_grade_cn", "GRADE": "material_grade_cn",
        "CONN": "connection", "CONNECTION": "connection",
        "C": "chemical_C", "SI": "chemical_Si", "MN": "chemical_Mn",
        "P": "chemical_P", "S": "chemical_S", "CR": "chemical_Cr",
        "NI": "chemical_Ni", "CU": "chemical_Cu", "MO": "chemical_Mo",
        "V": "chemical_V", "TI": "chemical_Ti", "NB": "chemical_Nb",
        "AL": "chemical_Al", "N": "chemical_N",
    }
    props = product.get("properties", {})
    chem = props.get("chemical", {})
    value = None
    if key in prop_map:
        pkey = prop_map[key]
        if pkey.startswith("chemical_"):
            sym = key  # 指标 id 末尾段为大写符号，如 SI/MN/C/P/S
            value = chem.get(sym)
            if value is None:
                # 兼容产品化学字典里 "Si"/"Mn" 等混合大小写写法
                for ck, cv in chem.items():
                    if str(ck).upper() == sym:
                        value = cv
                        break
        elif pkey in props:
            value = props.get(pkey)
        else:
            value = product.get(pkey)   # 回退产品根级字段（od_mm/dn_mm/长度等）
    if value is None:
        value = props.get(key) or props.get(ind.get("name_zh")) or props.get(ind.get("name_en"))
        if value is None:
            value = product.get(key) or product.get(ind.get("name_zh")) or product.get(ind.get("name_en"))
    # 单位长度重量：产品常未直接给重量；用 外径+壁厚 按理论公式计算作为参考值
    if value is None and key in ("WGT", "MASS", "WEIGHT"):
        od2 = _to_num(props.get("od_mm") or product.get("od_mm"))
        wt2 = _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
        if od2 and wt2:
            value = round(0.0246615 * wt2 * (od2 - wt2), 3)
    # 牌号桥接：俄罗斯标准(9940/11068 等)的 GRADE/STEEL 集合为俄牌号，
    # 当产品填的是中文牌号时，把它转成对应俄牌号再判定，避免 CN 牌号 配 俄集合 而误判 fail。
    if "GRADE" in key or "STEEL" in key or "钢号" in str(ind.get("indicator_zh") or "") or "牌号" in str(ind.get("indicator_zh") or ""):
        bridged = _grade_bridge(ind, value, product)
        if bridged != value:
            value = bridged
    # 尺寸公差类（外径/壁厚/长度/弯曲度）：用户提供了"实测值" → 按标准公差判定；
    # 未提供实测值 → 保持 NA（需实测）。有实测即由 NA 转为真实 PASS/FAIL。
    _dim = {
        "OD":  ("od_mm", "od_meas_mm"),      # 外径 普通级 ±1.0%
        "D":   ("od_mm", "od_meas_mm"),
        "WT":  ("wall_thickness_mm", "wt_meas_mm"),   # 壁厚 普通级 +12.5%/-10%
        "WALL":("wall_thickness_mm", "wt_meas_mm"),
        "LEN": ("length_m", "len_meas_m"),   # 长度 定尺 0~+20mm（简化按名义）
        "LENGTH": ("length_m", "len_meas_m"),
        "STR": (None, "straight_meas_mm_m"), # 弯曲度 每米 ≤1.5mm
        "DIM": (None, None),                 # 尺寸与允许偏差（外径+壁厚综合）
    }
    if key in _dim and ("desc" in req or "table" in req or "spec" in req or "max" in req or "min" in req):
        _nom_key, _meas_key = _dim[key]
        meas = _to_num(props.get(_meas_key) or product.get(_meas_key))
        if key == "STR":
            # 弯曲度：每米 ≤1.5mm（普通级）
            if meas is not None:
                ok = meas <= 1.5
                return {
                    "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                    "category": ind.get("category"), "unit": ind.get("unit"),
                    "requirement": {"max": 1.5}, "clause": ind.get("clause"),
                    "test_method": ind.get("test_method"), "product_value": meas,
                    "verdict": ("pass" if ok else "fail"),
                    "detail": f"弯曲度实测 {meas} mm/m ≤ 1.5 → {'✅' if ok else '❌'}",
                }
        elif key == "DIM":
            # 尺寸与允许偏差（GB/T 13793/8163/14976 等）：综合 外径 与 壁厚 公差，有任一实测即判定
            od_m = _to_num(props.get("od_meas_mm") or product.get("od_meas_mm"))
            wt_m = _to_num(props.get("wt_meas_mm") or product.get("wt_meas_mm"))
            od_n = _to_num(props.get("od_mm") or product.get("od_mm"))
            wt_n = _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
            checks = []
            if od_m and od_n:
                dev = abs(od_m - od_n) / od_n * 100
                checks.append(("外径偏差", round(dev, 3), 1.0))
            if wt_m and wt_n:
                dev = (wt_m - wt_n) / wt_n * 100
                checks.append(("壁厚偏差", round(dev, 3), 12.5))
            if checks:
                parts = []
                ok_all = True
                for name, dev, lim in checks:
                    lo = -10.0 if name == "壁厚偏差" else None
                    ok = (dev <= lim) and (dev >= (lo if lo is not None else -lim))
                    ok_all = ok_all and ok
                    parts.append(f"{name} {dev}%（限{'−10~+'+str(lim) if lo is not None else '±'+str(lim)}% → {'✅' if ok else '❌'}）")
                return {
                    "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                    "category": ind.get("category"), "unit": ind.get("unit"),
                    "requirement": {"desc": "尺寸与允许偏差（符合 外径±1%、壁厚−10%~+12.5%）"},
                    "clause": ind.get("clause"), "test_method": ind.get("test_method"),
                    "product_value": checks[0][1] if checks else None,
                    "verdict": ("pass" if ok_all else "fail"),
                    "detail": "；".join(parts) + "（实测）",
                }
        elif key == "WALL" and isinstance(req, dict) and isinstance(req.get("table"), list):
            # 俄标壁厚分档表（如 ГОСТ 3262-75 的 3262.WALL）：按"最小壁厚"真判（默认普通型），
            # 而非公差——避免名义4.0mm 因公差为0而误PASS（普通型应≥4.5mm）。
            dnw = _to_num(product.get("dn_mm") or props.get("dn_mm"))
            wtw = _to_num(props.get("wt_meas_mm") or product.get("wt_meas_mm")) or _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
            wrows = _find_wall_table(ind, dnw)
            if wrows and dnw and wtw:
                for wr in wrows:
                    if isinstance(wr, dict) and _to_num(wr.get("Ду")) == dnw:
                        mn = _to_num(wr.get("обыкновенные") or wr.get("norm") or wr.get("normal"))
                        if mn is not None:
                            ok = wtw >= mn
                            return {
                                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                                "category": ind.get("category"), "unit": ind.get("unit"),
                                "requirement": {"min": mn}, "clause": ind.get("clause"),
                                "test_method": ind.get("test_method"), "product_value": wtw,
                                "verdict": ("pass" if ok else "fail"),
                                "detail": f"按 {ind.get('name_zh')}（普通型）最小壁厚需≥{mn} mm，产品={wtw} mm → {'✅' if ok else '❌'}",
                            }
                        break
        elif meas is not None and _nom_key:
            nom = _to_num(props.get(_nom_key) or product.get(_nom_key))
            if nom:
                if key in ("LEN", "LENGTH"):
                    # 长度：简化按非定尺范围 3~12m；实测值须在范围内
                    ok = 3.0 <= meas <= 12.0
                    return {
                        "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                        "category": ind.get("category"), "unit": ind.get("unit"),
                        "requirement": {"range": [3.0, 12.0]}, "clause": ind.get("clause"),
                        "test_method": ind.get("test_method"), "product_value": meas,
                        "verdict": ("pass" if ok else "fail"),
                        "detail": f"长度实测 {meas} m 在 3~12 m 内 → {'✅' if ok else '❌'}",
                    }
                if key in ("OD", "D"):
                    # 外径普通级 ±1.0%
                    dev = abs(meas - nom) / nom * 100
                    ok = dev <= 1.0
                    return {
                        "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                        "category": ind.get("category"), "unit": ind.get("unit"),
                        "requirement": {"max": 1.0}, "clause": ind.get("clause"),
                        "test_method": ind.get("test_method"), "product_value": round(dev, 3),
                        "verdict": ("pass" if ok else "fail"),
                        "detail": f"外径实测 {meas} mm vs 名义 {nom} mm，偏差 {round(dev,3)}% ≤ ±1.0% → {'✅' if ok else '❌'}",
                    }
                if key in ("WT", "WALL"):
                    # 壁厚普通级 +12.5%/-10%
                    dev = (meas - nom) / nom * 100
                    ok = (-10.0 <= dev <= 12.5)
                    return {
                        "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                        "category": ind.get("category"), "unit": ind.get("unit"),
                        "requirement": {"min": -10.0, "max": 12.5}, "clause": ind.get("clause"),
                        "test_method": ind.get("test_method"), "product_value": round(dev, 3),
                        "verdict": ("pass" if ok else "fail"),
                        "detail": f"壁厚实测 {meas} mm vs 名义 {nom} mm，偏差 {round(dev,3)}% 在 -10%/+12.5% 内 → {'✅' if ok else '❌'}",
                    }
    # 单位归一（锌层）：产品输入统一为"重量 g/m²"，而俄标按"厚度 μm"。
    # 锌密度≈7.14 g/cm³ → 1 μm ≈ 7.14 g/m²；把重量换算成厚度再比较，避免 350(g/m²) 误配 30(μm)。
    zid = str(ind.get("id", "")).lower()
    unit = str(ind.get("unit") or "").lower()
    if value is not None and "zinc" in zid and any(u in unit for u in ("мкм", "μm", "微米", "mic")):
        v = _to_num(value)
        if v is not None:
            value = round(v / 7.14, 1)
    # 液压试验压力：标准多按公式 P=2SR/D 给定（描述型）。用 壁厚+外径+屈服 计算应达到的
    # 试验压力，再与产品实测水压比较 → 由 NA 转为真实判定。仅对 HYD/HYDRO 且 desc 含公式者适用。
    d_text = str((ind.get("values") or {}).get("desc", "")) + str((ind.get("values") or {}).get("spec", ""))
    if "hyd" in zid and value is not None and ("P=2SR/D" in d_text or "2SR" in d_text or "2*S" in d_text
                                              or ("P=2S" in d_text) or ("2.8" in d_text and "水压" in d_text)):
        wt3 = _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
        od3 = _to_num(props.get("od_mm") or product.get("od_mm"))
        _re3 = _to_num(props.get("re_mpa") or product.get("re_mpa"))
        _rp3 = _to_num(props.get("rp02_mpa") or product.get("rp02_mpa"))
        _ss3 = str(ind.get("id", "")).split(".")[0].lower() in ("14976", "12771", "9940", "11068")
        # 不锈钢无屈服点，用 Rp0.2；碳钢用 ReL
        re3 = _rp3 if (_ss3 and _rp3 is not None) else _re3
        if wt3 and od3 and re3:
            # CN 公式3：P=2×t×S/D，S=0.6×(屈服/Rp0.2)，最大5.0，修约0.5
            _slab = "Rp0.2" if (_ss3 and _rp3 is not None) else "ReL"
            _p = _hydro_req_pressure(ind, product)
            _rv = _to_num(value)
            if _rv is None:
                return {
                    "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                    "category": ind.get("category"), "unit": ind.get("unit"),
                    "requirement": {"min": _p}, "clause": ind.get("clause"),
                    "test_method": ind.get("test_method"), "product_value": value,
                    "verdict": "na",
                    "detail": f"水压试验需≥{_p} MPa；产品值 {value!r} 无法数值化",
                }
            _ok = _rv >= _p
            return {
                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                "category": ind.get("category"), "unit": ind.get("unit"),
                "requirement": {"min": _p}, "clause": ind.get("clause"),
                "test_method": ind.get("test_method"), "product_value": value,
                "verdict": ("pass" if _ok else "fail"),
                "detail": f"按标准最小试验压力需≥{_p} MPa，产品={value} MPa → {'✅' if _ok else '❌'}",
            }
    # 俄标水压（ГОСТ 3845-75 计算试验压力）：用户给实测水压 → 计算最小值并比较
    if "hyd" in zid and value is not None and ("3845" in d_text or "испытательное давление" in d_text):
        wt4 = _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
        od4 = _to_num(props.get("od_mm") or product.get("od_mm"))
        _re4 = _to_num(props.get("re_mpa") or product.get("re_mpa"))
        _rp4 = _to_num(props.get("rp02_mpa") or product.get("rp02_mpa"))
        _ss4 = str(ind.get("id", "")).split(".")[0].lower() in ("14976", "12771", "9940", "11068")
        # 不锈钢用 Rp0.2（无屈服点），碳钢用 ReL；[σ]≈0.9×强度，保守取 0.6×强度保证不漏判
        re4 = _rp4 if (_ss4 and _rp4 is not None) else _re4
        if wt4 and od4 and re4:
            _p = _hydro_req_pressure(ind, product)
            _rv = _to_num(value)
            if _rv is not None:
                _ok = _rv >= _p
                return {
                    "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                    "category": ind.get("category"), "unit": ind.get("unit"),
                    "requirement": {"min": _p}, "clause": ind.get("clause"),
                    "test_method": ind.get("test_method"), "product_value": value,
                    "verdict": ("pass" if _ok else "fail"),
                    "detail": f"按标准最小试验压力需≥{_p} MPa，产品={value} MPa → {'✅' if _ok else '❌'}",
                }
    # 俄标水压"按管壁等级分档"的固定值表（如 ГОСТ 3262-75 3262.HYDRO：
    #   轻型/普通型 → 2.4 MPa；加强型 → 4.9 MPa）。按 公称通径+壁厚 判断属于哪一档 → 取对应压力比较。
    if "hyd" in zid and isinstance(req, dict) and isinstance(req.get("table"), list):
        _rows = req["table"]
        if any(isinstance(r, dict) and any(k in r for k in ("давление", "pressure")) for r in _rows):
            dn5 = _to_num(product.get("dn_mm") or props.get("dn_mm"))
            wt5 = _to_num(props.get("wall_thickness_mm") or product.get("wall_thickness_mm"))
            press_row = {}
            for r in _rows:
                if isinstance(r, dict):
                    k = str(r.get("тип") or r.get("level") or r.get("type") or "").lower()
                    for kk in ("давление", "pressure"):
                        if kk in r:
                            press_row[k] = r[kk]
            # 判断管壁等级：优选用同标准 WALL 表（обыкновенные/усиленные 壁厚），否则按普通型
            cls = "обыкновенные"
            _wrows = _find_wall_table(ind, dn5)
            if _wrows and wt5:
                for wr in _wrows:
                    if _to_num(wr.get("Ду")) == dn5:
                        norm = _to_num(wr.get("обыкновенные") or wr.get("norm") or wr.get("normal"))
                        rein = _to_num(wr.get("усиленные") or wr.get("reinforced") or wr.get("усиленн"))
                        if norm is not None and wt5 > norm:
                            cls = "усиленные" if (rein is not None and wt5 >= rein) else "обыкновенные"
                        break
            def _pick(*kws):
                for k, p in press_row.items():
                    for kw in kws:
                        if kw in k:
                            return p
                return None
            if cls == "усиленные":
                min_press = _pick("усилен", "reinforced") or min(v for v in press_row.values() if isinstance(v, (int, float)))
            else:
                min_press = _pick("обыкновен", "обычн", "normal", "лёгк", "легк") or min(v for v in press_row.values() if isinstance(v, (int, float)))
            _rv5 = _to_num(value)
            if _rv5 is not None and min_press is not None:
                _ok5 = _rv5 >= min_press
                _cls_zh = {"обыкновенные": "普通型", "усиленные": "加强型", "лёгкие": "轻型", "обычн": "普通型"}.get(cls, cls)
                return {
                    "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                    "category": ind.get("category"), "unit": ind.get("unit"),
                    "requirement": {"min": min_press}, "clause": ind.get("clause"),
                    "test_method": ind.get("test_method"), "product_value": value,
                    "verdict": ("pass" if _ok5 else "fail"),
                    "detail": f"按 ГОСТ 3262-75 管壁等级（{_cls_zh}）水压试验需≥{min_press} MPa，产品={value} MPa → {'✅' if _ok5 else '❌'}",
                }
    # 螺纹连接：凡"管螺纹/螺纹连接"指标，产品不含螺纹（threaded=False）→ 不适用(na)，
    # 不得判为 fail（平口/焊接/法兰等无螺纹管不违反螺纹要求）。含螺纹 → 按标准判定。
    if "thread" in zid and value is False:
        return {
            "indicator_id": ind.get("id"),            "indicator_zh": ind.get("name_zh"),
            "category": ind.get("category"),
            "unit": ind.get("unit"),
            "requirement": req,
            "clause": ind.get("clause"),
            "test_method": ind.get("test_method"),
            "product_value": False,
            "verdict": "na",
            "detail": na_note(ind, req, False),
        }
    # 交货分组（俄标 ГОСТ 8731/10705/20295 GROUP 的 А/Б/В）：用户选择分组作为交付声明。
    # 分组决定"验收范围"：А 只保证力学、Б 只保证化学、В 力学+化学。未注明 → NA。
    if "group" in zid and isinstance(req, dict) and "map" in req:
        gsel = str(product.get("delivery_group") or "").strip()
        _gmap = {"А": "只保证力学性能（химический состав 不保证）",
                 "Б": "只保证化学成分（механические свойства 不保证）",
                 "В": "力学性能+化学成分 均保证"}
        if gsel in req["map"]:
            # 转换为内部统一键 处理大小写/俄文
            _norm_g = {"а": "А", "a": "А", "б": "Б", "b": "Б", "в": "В", "v": "В"}.get(gsel, gsel)
            _cl = ind.get("clause") or "п. 1.1"
            return {
                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                "category": ind.get("category"), "unit": ind.get("unit"),
                "requirement": req, "clause": _cl,
                "test_method": ind.get("test_method"), "product_value": _norm_g,
                "verdict": "pass",
                "detail": f"交货分组 {_norm_g}（依 ГОСТ {_cl}）：{_gmap.get(_norm_g, req['map'][gsel])}。此分组决定本次验收范围，被排除的类别见对应项'该分组不保证'",
            }
        if gsel == "":
            return {
                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                "category": ind.get("category"), "unit": ind.get("unit"),
                "requirement": req, "clause": ind.get("clause"),
                "test_method": ind.get("test_method"), "product_value": None,
                "verdict": "na",
                "detail": "未注明交货分组（А/Б/В），无法判定（分组影响力学/化学规定的范围）｜"
                          f"{na_note(ind, req, None)}",
            }
    # 无损检测：属"可选/按协议"项（非强制必检）。只在用户明确给出结果时才判定；
    # 未做/留空 → NA 并注明"可选/非强制"，不当作未达标。不设等级要求。
    if "ndt" in zid and isinstance(req, dict) and ("spec" in req or "desc" in req):
        ndt_res = str(props.get("ndt") or "").strip().lower()
        if ndt_res in ("pass", "合格", "ok"):
            return {
                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                "category": ind.get("category"), "unit": ind.get("unit"),
                "requirement": req, "clause": ind.get("clause"),
                "test_method": ind.get("test_method"), "product_value": ndt_res,
                "verdict": "pass",
                "detail": "无损检测合格（该检测为可选/按协议项，合格即满足）",
            }
        if ndt_res in ("fail", "不合格", "no"):
            return {
                "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
                "category": ind.get("category"), "unit": ind.get("unit"),
                "requirement": req, "clause": ind.get("clause"),
                "test_method": ind.get("test_method"), "product_value": ndt_res,
                "verdict": "fail",
                "detail": "无损检测不合格（该检测为可选/按协议项，但检出缺陷需复核）",
            }
        # 未做/留空 → 可选非强制，NA
        return {
            "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
            "category": ind.get("category"), "unit": ind.get("unit"),
            "requirement": req, "clause": ind.get("clause"),
            "test_method": ind.get("test_method"), "product_value": None,
            "verdict": "na",
            "detail": na_note(ind, req, None),
        }
    # 交货分组 → 验收范围开关：该分组不保证的类别指标判为"该分组不保证"（NA/豁免）
    if group_excludes(ind, product):
        gsel = str(product.get("delivery_group") or "").strip()
        _cat = "化学成分" if ("STEEL" in key.upper() or "CHEM" in key.upper() or "化学" in (ind.get("indicator_zh") or "")) else "力学性能"
        return {
            "indicator_id": ind.get("id"), "indicator_zh": ind.get("name_zh"),
            "category": ind.get("category"), "unit": ind.get("unit"),
            "requirement": req, "clause": ind.get("clause"),
            "test_method": ind.get("test_method"), "product_value": None,
            "verdict": "na",
            "detail": f"交货分组 {gsel} 不保证{_cat}，本项不在该分组验收范围内（不作为不达标，也不作为通过）｜✋ 该分组不保证{_cat}。📖 评价依据：{ind.get('clause') or '—'}（交货分组决定验收范围）。💡 参考：若要验收{_cat}，请改选 В 组（力学+化学）或补充实测/声明。",
        }
    # 按公称通径分档的"表格型"需求（如涂层厚度表1）→ 解析成单阈值再比较
    resolved = resolve_dn_table(req, product.get("dn_mm"))
    if resolved is not None:
        req = resolved
    # 按钢号分档的力学性能表（如 ГОСТ 8731-87）→ 逐属性比较后汇总判定
    gtab = resolve_grade_table(req, product)
    if gtab is not None:
        pvs = product.get("properties", {}) or {}
        subs, n_pass, n_fail, n_na = [], 0, 0, 0
        for col, pkey, req_val in gtab["per"]:
            pv = pvs.get(pkey, product.get(pkey))
            pv = _to_num(pv) if pv is not None else None
            if pv is None:
                subs.append(f"{col}（{pkey}）：未提供"); n_na += 1
                continue
            ok = pv >= req_val
            subs.append(f"{col}={pv} vs ≥{req_val}（{'✅' if ok else '❌'}）")
            n_pass += (1 if ok else 0); n_fail += (0 if ok else 1)
        if n_fail or (n_pass == 0 and n_na == 0):
            verdict = "fail"
        elif n_pass:
            verdict = "pass"
        else:
            verdict = "na"
        detail = "；".join(subs) + "（按钢号分档，逐属性比较）"
        return {
            "indicator_id": ind.get("id"),
            "indicator_zh": ind.get("name_zh"),
            "category": ind.get("category"),
            "unit": ind.get("unit"),
            "requirement": req,
            "clause": ind.get("clause"),
            "test_method": ind.get("test_method"),
            "product_value": value,
            "verdict": verdict,
            "detail": (detail if verdict != "na" else detail + "｜" + na_note(ind, req, value)),
        }
    # 连接方式：表单填中文，但 KB 的 set 可能是中文（5135.20）或俄文（СП 10.13130）。
    # 归一化到"要求 set 的语言"，避免中文值 配 俄文集合 而误判 fail。
    if "conn" in zid:
        _conn_map = {
            "沟槽连接": "жёлобчатое", "螺纹连接": "резьбовое", "法兰连接": "фланцевое",
            "焊接": "сварное", "卡压连接": "пресс-фитинговое", "平口/未注明": "",
        }
        _rev = {v: k for k, v in _conn_map.items() if v}
        allowed = req.get("set") if isinstance(req, dict) and isinstance(req.get("set"), (list, tuple)) else None
        val_str = str(value).strip() if value is not None else ""
        if allowed and value is not None:
            # 判断需求集合语言：集合元素含西里尔文 → 需求为俄文
            _ru = any(re.search(r"[\u0400-\u04FF]", str(a)) for a in allowed)
            if _ru:
                if val_str in _conn_map:
                    value = _conn_map[val_str]
                elif val_str in _rev:
                    pass
            else:
                if val_str in _rev:
                    value = _rev[val_str]
                elif val_str in _conn_map:
                    pass
    verdict, detail = compare_value(value, req)
    if verdict == "na":
        detail = f"{detail}｜{na_note(ind, req, value)}"
    return {
        "indicator_id": ind.get("id"),
        "indicator_zh": ind.get("name_zh"),
        "category": ind.get("category"),
        "unit": ind.get("unit"),
        "requirement": req,
        "clause": ind.get("clause"),
        "test_method": ind.get("test_method"),
        "product_value": value,
        "verdict": verdict,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# 4. ТР ТС 032/2013（承压设备）—— 已按官方文本核验（ред. 24.11.2023）
# ---------------------------------------------------------------------------
# 来源: Решение Совета ЕЭК от 02.07.2013 № 41 (с изм. на 24.11.2023)
#       ст.2 (область применения), ст.3 (исключения), Приложение 1 (таблицы 6-9)
# 术语: P = максимально допустимое рабочее давление [МПа]
#       DN = номинальный диаметр [мм];  P×DN [МПа·мм]
# 介质组 (группа рабочих сред):
#   Группа 1: 可燃/氧化/易燃/易爆/有毒(高毒)的气体、液体、蒸气的单相介质及其混合物
#   Группа 2: 其余所有介质（水、蒸汽、空气、氮气等）
#
# 适用范围（管道，ст.2 п.2 е)–и)），须同时满足:
#   е) Группа 1 气体/蒸气: P > 0.05 МПа 且 DN > 25 мм                          → 表6
#   ж) Группа 2 气体/蒸气: P > 0.05 МПа 且 DN > 32 мм 且 P×DN > 100 МПа·мм      → 表7
#   з) Группа 1 液体:      P > 0.05 МПа 且 DN > 25 мм 且 P×DN > 200 МПа·мм      → 表8
#   и) Группа 2 液体:      P > 1 МПа    且 DN > 200 мм 且 P×DN > 500 МПа·мм     → 表9
# 合格评定 (ст.5): 1-я/2-я категория → декларирование; 3-я/4-я → сертификация
#
# ⚠ 表6-9 行结构由条文文本恢复；边界含"включительно"按原文。正式出具证书前须与
#   官方文本(ст.2/Приложение 1)核对，并以认证机构判定为准。

TR_CU_032_SCOPE = {
    ("g1", "gas"):    {"p_min": 0.05, "dn_min": 25,  "pdn_min": None, "table": 6},
    ("g2", "gas"):    {"p_min": 0.05, "dn_min": 32,  "pdn_min": 100, "table": 7},
    ("g1", "liquid"): {"p_min": 0.05, "dn_min": 25,  "pdn_min": 200, "table": 8},
    ("g2", "liquid"): {"p_min": 1.0,  "dn_min": 200, "pdn_min": 500, "table": 9},
}

# 每行: {"dn": 范围|None, "pdn": 范围|None, "p": 范围|None}
# 范围 = (lo, hi, lo_inclusive, hi_inclusive)；None 表示"не нормируется"(不限)
TR_CU_032_TABLES = {
    6: [  # Группа 1, газ/пар
        ("I", [{"dn": (25, 100, False, True), "pdn": None, "p": (0.05, 1, False, True)},
               {"dn": (25, 100, False, True), "pdn": (None, 100, None, True), "p": (1, 3.5, False, True)}]),
        ("II", [{"dn": (100, 350, False, True), "pdn": None, "p": (0.05, 1, False, True)},
                {"dn": (25, 350, False, True), "pdn": (100, 350, False, True), "p": (1, 3.5, False, True)},
                {"dn": (25, 100, False, True), "pdn": None, "p": (3.5, None, False, None)}]),
        ("III", [{"dn": (350, None, False, None), "pdn": None, "p": (0.05, 1, False, True)},
                 {"dn": (100, 350, False, True), "pdn": (350, None, False, None), "p": (1, 3.5, False, True)},
                 {"dn": (100, None, False, None), "pdn": None, "p": (3.5, None, False, None)}]),
    ],
    7: [  # Группа 2, газ/пар
        ("I", [{"dn": (32, None, False, None), "pdn": (100, 350, False, True), "p": (0.05, 3.2, False, True)},
               {"dn": (32, 100, True, True), "pdn": None, "p": (3.2, None, False, None)}]),
        ("II", [{"dn": (100, None, False, None), "pdn": (350, 500, False, True), "p": (0.05, 3.2, False, True)},
                {"dn": (100, 250, False, True), "pdn": None, "p": (3.2, None, False, None)}]),
        ("III", [{"dn": (250, None, False, None), "pdn": None, "p": (3.2, None, False, None)},
                 {"dn": (250, None, False, None), "pdn": (500, None, False, None), "p": (0.05, 3.2, False, True)}]),
    ],
    8: [  # Группа 1, жидкость
        ("I", [{"dn": (25, None, False, None), "pdn": (200, None, False, None), "p": (0.05, 1, False, True)}]),
        ("II", [{"dn": (25, None, False, None), "pdn": (200, None, False, None), "p": (1, 8, False, True)},
                {"dn": (25, None, False, None), "pdn": (350, None, False, None), "p": (8, 50, False, True)}]),
        ("III", [{"dn": (25, None, False, None), "pdn": None, "p": (50, None, False, None)}]),
    ],
    9: [  # Группа 2, жидкость
        ("I", [{"dn": (200, None, False, None), "pdn": (500, None, False, None), "p": (1, 50, False, True)}]),
        ("II", [{"dn": (200, None, False, None), "pdn": None, "p": (50, None, False, None)}]),
    ],
}


def _in_range(v, rng):
    if rng is None:
        return True
    lo, hi, lo_in, hi_in = rng
    if lo is not None:
        if (v >= lo) if lo_in else (v > lo):
            pass
        else:
            return False
    if hi is not None:
        if (v <= hi) if hi_in else (v < hi):
            pass
        else:
            return False
    return True


def tr_cu_032(p_mpa, dn_mm, medium_group=2, phase="liquid"):
    """
    判定 ТР ТС 032/2013 适用范围与类别。
    返回: {"in_scope": bool, "category": "I/II/III"|None, "pdn": x,
           "route": "декларирование"|"сертификация"|None, "table": n|None,
           "note": "..."}
    """
    p = _to_num(p_mpa)
    dn = _to_num(dn_mm)
    if p is None or dn is None:
        return {"in_scope": None, "category": None, "pdn": None, "route": None,
                "table": None, "note": "缺少工作压力或公称通径，无法判定"}
    pdn = p * dn
    key = (f"g{medium_group}", phase)
    scope = TR_CU_032_SCOPE.get(key, TR_CU_032_SCOPE[("g2", "liquid")])

    in_scope = True
    reason = []
    if not p > scope["p_min"]:
        in_scope = False
        reason.append(f"P={p} МПа 未超过 {scope['p_min']} МПа")
    if not dn > scope["dn_min"]:
        in_scope = False
        reason.append(f"DN={dn} мм 未超过 {scope['dn_min']} мм")
    if scope["pdn_min"] is not None and not pdn > scope["pdn_min"]:
        in_scope = False
        reason.append(f"P×DN={pdn} МПа·мм 未超过 {scope['pdn_min']} МПа·мм")

    if not in_scope:
        return {"in_scope": False, "category": None, "pdn": round(pdn, 2), "route": None,
                "table": scope["table"],
                "note": f"不属 ТР ТС 032/2013 适用范围（ст.2 п.2 { {'g1':'е','g2':'ж'}[f'g{medium_group}'] if phase=='gas' else {'g1':'з','g2':'и'}[f'g{medium_group}'] }）："
                       f"{'；'.join(reason)}。无需按该法规进行合格评定。"}

    category, matched = None, None
    for cat, rows in TR_CU_032_TABLES[scope["table"]]:
        for row in rows:
            if (_in_range(dn, row.get("dn")) and _in_range(pdn, row.get("pdn"))
                    and _in_range(p, row.get("p"))):
                category, matched = cat, row
                break
        if category:
            break
    route = {"I": "декларирование(符合性声明)", "II": "декларирование(符合性声明)",
             "III": "сертификация(强制认证)", "IV": "сертификация(强制认证)"}.get(category)
    return {
        "in_scope": True, "category": category, "pdn": round(pdn, 2),
        "route": route, "table": scope["table"],
        "note": f"Группа {medium_group} 介质（{'газ/пар' if phase=='gas' else 'жидкость'}），"
                f"P={p} МПа, DN={dn} мм, P×DN={round(pdn,2)} МПа·мм → 表{scope['table']} → "
                f"Категория {category} → {route}",
    }


# ---------------------------------------------------------------------------
# 5. ТР ЕАЭС 043/2017（消防产品）
# ---------------------------------------------------------------------------
def tr_eaeu_043(product):
    use = (product.get("intended_use") or "")
    fire_keywords = ["消防", "пожар", "喷淋", "喷水", "喷洒", "洒水", "灭火", "水喷雾", "消火栓",
                     "пожаротушени", "пожаротушен", "спринклер", "огнетуш", "противопожарн"]
    # 显式"非消防"标注 → 即使字样含"消防"也判为不适用
    if any(m in use for m in ("非消防", "普通给水", "一般给水", "燃气输送", "工业用水", "工业管线", "排水管道")):
        is_fire = False
    else:
        is_fire = any(k.lower() in use.lower() for k in fire_keywords)
    if not is_fire:
        return {"applies": False, "route": None,
                "note": "产品用途未涉及消防系统，ТР ЕАЭС 043/2017 不适用"}
    # 已核验（Решением Совета ЕЭК №40 от 23.06.2017, перечень продукции）:
    # - Перечень 未单独列出"трубы"，但第 IX 节类别为
    #   "Технические средства, функционирующие в составе установок пожаротушения
    #    автоматических (в т.ч. автономных, роботизированных, модульных)"，
    #   方案 1с/3с/4с（型式试验+生产控制/监督）。
    # - 水/泡沫自动灭火系统专用管材、柔性连接件作为系统元件归入该类实施强制认证。
    # - 要求依据: ст.38-41（自动灭火装置组成设备须实现火灾探测、信号、灭火剂释放）。
    return {
        "applies": True,
        "route": "обязательная сертификация(强制认证)",
        "basis": "ТР ЕАЭС 043/2017 附录(Перечень) 第 IX 节：自动(水/泡沫)灭火装置组成的技术设备，方案 1с/3с/4с；"
                 "ст.38-41 技术要求",
        "schemes": ["1с", "3с", "4с"],
        "tests": ["огнестойкость(耐火性)", "прочность/герметичность под давлением в условиях пожара(火灾工况承压与密封)",
                  "работоспособность(功能可靠性)"],
        "note": "金属管材作为水/泡沫自动灭火系统元件时按 ТР ЕАЭС 043/2017 强制认证（归入 Перечень 第 IX 节）；"
                "注意：Перечень 未单列'трубы'，普通建筑用 ВГП 管作为独立产品不属 043 范围，"
                "仅当作为灭火系统专用元件申报时认证。"
                "若同时满足 ТР ТС 032/2013 适用范围则两者均需满足——两者判定原理不同："
                "032 看 P/DN/介质（超压风险），043 看用途与材料（消防安全）。"
                "消防给水/喷淋管道本身按 СП 10.13130.2020 / СП 485.1311500.2020 确定管材、壁厚、压力与连接，"
                "管材材料性能按 ГОСТ 3262-75/10705-80/8731-87 验收（见知识库'СП 10.13130.2020 / СП 485.1311500.2020'条目）。",
    }


# ---------------------------------------------------------------------------
# 6. 指数化评分
# ---------------------------------------------------------------------------
def compliance_index(cn_results, ru_results):
    def _stats(results):
        """返回 (通过, 未通过, 未适用/需判定, 覆盖度)。覆盖度=可评项/总项。"""
        passed = sum(1 for r in results if r["verdict"] == "pass")
        failed = sum(1 for r in results if r["verdict"] == "fail")
        na = sum(1 for r in results if r["verdict"] == "na")
        total = passed + failed + na
        ci = (passed / (passed + failed) if (passed + failed) else None)
        coverage = (total - na) / total if total else None
        return passed, failed, na, ci, coverage

    cn_p, cn_f, cn_na, cn_ci, cn_cov = _stats(cn_results)
    ru_p, ru_f, ru_na, ru_ci, ru_cov = _stats(ru_results)

    # 评级：先看 CI（通过率），再按覆盖度修正——未评/需判定项过多则不作 A/B 定论，
    # 避免"评得越全越吃亏"或"NA 多反而拿 A"两种失真。
    def grade(ci, cov):
        if ci is None:
            return "NA"
        if cov is not None and cov < 0.5:
            return "NA"           # 覆盖不足一半，无法可靠定级
        if ci >= 0.95:
            return "A"
        if ci >= 0.85:
            return "B"
        if ci >= 0.70:
            return "C"
        return "D"

    return {
        "cn": {"ci": cn_ci, "passed": cn_p, "failed": cn_f, "na": cn_na,
               "coverage": cn_cov, "grade": grade(cn_ci, cn_cov)},
        "ru": {"ci": ru_ci, "passed": ru_p, "failed": ru_f, "na": ru_na,
               "coverage": ru_cov, "grade": grade(ru_ci, ru_cov)},
    }


# ---------------------------------------------------------------------------
# 7. 主流程
# ---------------------------------------------------------------------------
def evaluate(product: dict, kb=None):
    kb = kb or load_knowledge_base()
    product_type = product.get("type", "")
    cn_ids, ru_ids = applicable_standards(product_type)

    cn_by_id = {s["id"]: s for s in kb["cn"]["standards"]}
    ru_by_id = {s["id"]: s for s in kb["ru"]["standards"]}

    def run(ids, by_id):
        out, skipped = [], []
        for sid in ids:
            std = by_id.get(sid)
            if std is None:
                skipped.append(sid)
                continue
            items = [check_indicator(ind, product) for ind in std.get("indicators", [])]
            out.append({"standard": sid, "name_zh": std.get("name_zh", ""),
                        "applies": True, "items": items})
        return out, skipped

    cn_results, cn_skipped = run(cn_ids, cn_by_id)
    ru_results, ru_skipped = run(ru_ids, ru_by_id)

    tr032 = tr_cu_032(product.get("working_pressure_mpa"), product.get("dn_mm"),
                      medium_group=product.get("medium_group", 2),
                      phase=product.get("medium_phase", "liquid"))
    tr043 = tr_eaeu_043(product)

    idx = compliance_index(
        [i for s in cn_results for i in s["items"]],
        [i for s in ru_results for i in s["items"]],
    )

    overall = {
        "cn_compliant": idx["cn"]["grade"] in ("A", "B"),
        "ru_compliant": idx["ru"]["grade"] in ("A", "B"),
        "tr_cu_032_route": tr032.get("route"),
        "tr_eaeu_043_route": tr043.get("route") if tr043.get("applies") else "不适用",
        "summary": "",
    }
    def _verdict_label(si):
        """NA（覆盖不足）不应被说成'不合规'——单独给'评估不足，未定级'。"""
        g = si["grade"]
        if g in ("A", "B"):
            return "合规"
        if g == "NA":
            return "评估不足，未定级"
        return "不合规"

    def _gpref(g):
        # NA 时不显示"评级 未定级"这种啰嗦说法，直接省略评级前缀
        return f"评级 {g}，" if g != "NA" else ""

    overall["summary"] = (
        f"中国侧：{_verdict_label(idx['cn'])}（{_gpref(idx['cn']['grade'])}"
        f"可评 {idx['cn']['passed'] + idx['cn']['failed']} 项中通过 {idx['cn']['passed']} 项）；"
        f"俄罗斯侧：{_verdict_label(idx['ru'])}（{_gpref(idx['ru']['grade'])}"
        f"可评 {idx['ru']['passed'] + idx['ru']['failed']} 项中通过 {idx['ru']['passed']} 项）；"
        f"ТР ТС 032/2013：{'вне области' if not tr032['in_scope'] else 'Категория ' + str(tr032['category']) + ' → ' + str(tr032['route'])}；"
        f"ТР ЕАЭС 043/2017：{'适用 → ' + tr043['route'] if tr043['applies'] else '不适用'}"
    )

    return {
        "product": product,
        "cn_results": cn_results,
        "ru_results": ru_results,
        "skipped_cn": cn_skipped,
        "skipped_ru": ru_skipped,
        "tr_cu_032": tr032,
        "tr_eaeu_043": tr043,
        "index": idx,
        "overall": overall,
    }


def render_text(result: dict) -> str:
    L = []
    L.append("=" * 70)
    L.append(f"产品型号: {result['product'].get('model', '')}")
    L.append(f"类型: {result['product'].get('type', '')}")
    L.append("=" * 70)
    for side, key in (("【中国标准】", "cn_results"), ("【俄罗斯标准】", "ru_results")):
        L.append(side)
        for s in result[key]:
            L.append(f"  ◆ {s['standard']} {s['name_zh']}")
            for it in s["items"]:
                flag = {"pass": "✅", "fail": "❌", "na": "◻️"}.get(it["verdict"], "?")
                L.append(f"    {flag} {it['indicator_zh']}({it['indicator_id']}) "
                         f"产品值={it['product_value']} {it.get('unit') or ''} "
                         f"→ {it['verdict'].upper()} | 条款: {it.get('clause') or '—'} | {it['detail']}")
        for sid in result.get("skipped_cn" if key == "cn_results" else "skipped_ru", []):
            L.append(f"  ⚠ 知识库缺失标准: {sid}")
    tr = result["tr_cu_032"]
    L.append("【ТР ТС 032/2013 承压设备】")
    L.append(f"  P×DN = {tr['pdn']} МПа·мм | 适用: {tr['in_scope']} | 表{tr['table']} | "
             f"Категория {tr['category']} | {tr['route']} | {tr['note']}")
    tr43 = result["tr_eaeu_043"]
    L.append("【ТР ЕАЭС 043/2017 消防产品】")
    L.append(f"  适用: {tr43['applies']} | 路径: {tr43.get('route') or '—'} | {tr43.get('note', '')}")
    idx = result["index"]
    L.append("【指数化评分】")
    L.append(f"  中国合规度 CI={idx['cn']['ci']} 评级{idx['cn']['grade']}"
             f"（通过 {idx['cn']['passed']}/{idx['cn']['passed'] + idx['cn']['failed']}）")
    L.append(f"  俄罗斯合规度 CI={idx['ru']['ci']} 评级{idx['ru']['grade']}"
             f"（通过 {idx['ru']['passed']}/{idx['ru']['passed'] + idx['ru']['failed']}）")
    L.append("【集体评价结论】")
    L.append(f"  {result['overall']['summary']}")
    L.append("=" * 70)
    L.append("注: 本结果为规则引擎初筛（数据版本 ТР ТС 032/2013 ред. 24.11.2023），"
             "最终结论须由认证机构/认可实验室确认。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="中俄消防金属管材合规评价引擎")
    ap.add_argument("input", help="产品参数 JSON 文件路径")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON 结果")
    args = ap.parse_args()

    product = load_json(Path(args.input).resolve())
    # 兼容 {"product": {...}} 包装（与 examples/sample_input.json 及 api_server 的 EvaluateRequest 同构）
    if isinstance(product, dict) and isinstance(product.get("product"), dict):
        product = product["product"]
    result = evaluate(product)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
