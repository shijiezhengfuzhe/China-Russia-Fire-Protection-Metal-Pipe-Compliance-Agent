#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG 对话层（rag_chat.py）
=========================
借鉴主流开源 RAG 对话（RAGFlow / Danswer / AnythingLLM / documind 等）的通用做法：

  1) 索引   ：把 标准 / 指标 / 技术法规 切成 chunk，字段加权（标题 > 条款·方法 > 正文）；
  2) 检索   ：BM25（tf 饱和 + idf + 长度归一）+ 字段加权 + 标准号/短语 **重排(rerank)**；
  3) 查询理解：意图路由（参数评价 / 条款查询 / 法规适用 / 帮助）+ 多轮上下文(history)承接；
  4) 回答   ：结构化（结论 → 依据 → 来源 → 延伸），带 **引用片段 snippet + 条款号 + 测试方法**；
  5) 体验   ：来源引用、追问建议(followups)、可选流式(SSE)。

不依赖外部向量库 / 大模型；纯标准库。接大模型后可直接把 refs 作为上下文喂给模型。
"""
import re
import json
import math
from typing import Optional

import evaluate as E


# ---------------------------------------------------------------------------
# 1. 分词 / 取值渲染
# ---------------------------------------------------------------------------
def _tok(s) -> list:
    """分词：拉丁/西里尔/数字按词；中文按 2-gram（降低单字噪声）。"""
    s = str(s or "")
    out = [t.lower() for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\.\-]*", s)]
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            out.append(run)
        else:
            out += [run[i:i + 2] for i in range(len(run) - 1)]
    return [t for t in out if t.strip()]


# 表格/字典里的俄文列名 → 中文（让不懂俄语的用户也能看懂）
_COL_ZH = {
    "Ду": "DN", "DN": "DN", "OD": "外径", "D": "外径",
    "лёгкие": "轻型", "обыкновенные": "普通型", "усиленные": "加强型",
    "сталь": "钢号", "класс": "级别", "марка": "牌号",
    "σв": "σв(抗拉)", "σт": "σт(屈服)", "δ5": "δ5(伸长率)", "KCU": "KCU(冲击)",
    "тип": "类型", "давление": "压力", "pressure": "压力",
    "od_grade": "外径等级", "wt_grade": "壁厚等级",
    "tolerance_pct": "偏差%", "plus_pct": "上偏差%", "minus_pct": "下偏差%",
    "d_range": "口径范围", "min_g_m2": "最小克重 g/m²",
}


def _col(k, lang: str) -> str:
    return str(k) if lang == "ru" else _COL_ZH.get(str(k), str(k))


def _row_text(row: dict, lang: str = "zh") -> str:
    """把一行字典渲染成"列名 值"的可读文本（中文界面翻译俄文列名）。"""
    parts = []
    for k, x in row.items():
        if x is None or x == "":
            continue
        parts.append(f"{_col(k, lang)} {x}")
    return "，".join(parts)


_NEED_BY_KEY = {
    "RM": "抗拉强度 Rm", "REL": "屈服强度 ReL", "RE": "屈服强度 ReL", "REH": "屈服强度 ReH",
    "A": "断后伸长率 A", "EL": "断后伸长率 A", "RP02": "Rp0.2", "RP": "Rp0.2",
    "OD": "实测外径", "WT": "实测壁厚", "WALL": "实测壁厚（或壁厚等级）",
    "LEN": "实测长度", "LENGTH": "实测长度", "STR": "实测弯曲度", "DIM": "实测外径/壁厚",
    "WGT": "单位长度重量", "MASS": "单位长度重量", "WEIGHT": "单位长度重量",
    "FLAT": "压扁试验结果", "FLATTEN": "压扁试验结果", "BEND": "弯曲试验结果",
    "EXPAND": "扩口试验结果", "ZINC": "镀锌层重量 g/m²", "GALV": "镀锌层",
    "HYD": "水压试验压力 MPa", "HYDRO": "水压试验压力 MPa",
    "NDT": "无损检测结果（可选）", "GROUP": "交货分组 А/Б/В", "CLASS": "级别",
    "GRADE": "牌号", "STEEL": "牌号", "CHEM": "化学成分", "MECH": "力学性能",
    "TECH": "工艺试验结果", "COAT_THK": "涂层厚度", "COAT_ADH": "涂层附着力",
    "THREAD": "螺纹（若为螺纹连接）", "CONN": "连接方式", "DN": "公称通径", "DU": "公称通径",
}


_NEED_RU = {
    "RM": "σв (предел прочности)", "REL": "σт (предел текучести)", "RE": "σт", "REH": "σт",
    "A": "δ5 (относительное удлинение)", "EL": "δ5", "RP02": "Rp0.2",
    "OD": "фактический наружный диаметр", "WT": "фактическая толщина стенки", "WALL": "фактическая толщина стенки",
    "LEN": "фактическая длина", "LENGTH": "фактическая длина", "STR": "фактическая кривизна",
    "DIM": "фактические диаметр/толщина", "WGT": "масса 1 м", "MASS": "масса 1 м",
    "FLAT": "результат испытания на сплющивание", "FLATTEN": "результат испытания на сплющивание",
    "EXPAND": "результат испытания на раздачу", "BEND": "результат испытания на изгиб",
    "ZINC": "масса цинкового покрытия, г/м²", "GALV": "цинковое покрытие",
    "HYD": "испытательное давление, МПа", "HYDRO": "испытательное давление, МПа",
    "NDT": "результат НК (опционально)", "GROUP": "группа поставки А/Б/В", "CLASS": "класс",
    "GRADE": "марка стали", "STEEL": "марка стали", "CHEM": "химический состав",
    "MECH": "механические свойства", "TECH": "результаты технологических испытаний",
    "COAT_THK": "толщина покрытия", "COAT_ADH": "адгезия покрытия",
    "THREAD": "резьба (при резьбовом соединении)", "CONN": "тип соединения",
    "DN": "номинальный диаметр", "DU": "номинальный диаметр",
}


def _need_hint(it, lang: str = "zh") -> str:
    """从指标 id 推断"这项要补什么参数"（用于缺数据清单）。"""
    ru = (lang == "ru")
    iid = str(it.get("indicator_id") or it.get("id") or "").upper()
    parts = iid.split(".")
    last = parts[-1] if parts else ""
    _SYM = {"C": "C", "SI": "Si", "MN": "Mn", "P": "P", "S": "S", "CR": "Cr", "NI": "Ni",
            "MO": "Mo", "CU": "Cu", "V": "V", "TI": "Ti", "NB": "Nb", "AL": "Al", "N": "N"}
    if "CHEM" in parts and last in _SYM:
        return (f"химический состав {_SYM[last]}" if ru else f"化学成分 {_SYM[last]}")
    return (_NEED_RU if ru else _NEED_BY_KEY).get(last, "")


_UNIT_ZH = {"мкм": "μm", "мм": "mm", "МПа": "MPa", "м": "m", "кг": "kg", "г": "g", "мм/м": "mm/m"}


def _unit_zh(u: str) -> str:
    """俄文单位 → 中文界面用符号。"""
    u = (u or "").strip()
    return _UNIT_ZH.get(u, u)


def _edge_short_safe(detail: str) -> str:
    """取失败项的 ❌ 子项；没有子项则截断展示。"""
    subs = re.findall(r"[^；;]*（❌）", detail or "")
    if subs:
        return "；".join(x.strip() for x in subs)
    return (detail or "")[:80]


def _na_reason(detail: str) -> str:
    """从 NA 的 detail 里抽出「✋ 原因」。"""
    m = re.search(r"✋\s*(.*?)。📖", detail or "", re.S)
    return m.group(1) if m else ""


_NAME_RU = {}


def _item_name(it, lang: str = "zh") -> str:
    """按语言取指标名：俄语优先用知识库/映射里的俄文名（中国 GB 指标走 cn_indicator_ru.json）。"""
    nm = it.get("indicator_zh") or it.get("indicator_id")
    if lang == "ru":
        return _NAME_RU.get(it.get("indicator_id")) or nm
    return nm


def _flag(side: str, lang: str = "zh") -> str:
    """来源标记：不用国旗 emoji（部分环境会渲染成 ru/cn 字母）。"""
    if side == "cn":
        return "【中】" if lang == "zh" else "[КНР] "
    if side == "faq":
        return "【FAQ】" if lang == "zh" else "[FAQ] "
    return "【俄】" if lang == "zh" else "[РФ] "


def _clause_text(c, lang: str = "zh") -> str:
    """条款号：中文界面把俄文"Таблица/раздел"等词译成中文（编号保留）。"""
    if not c:
        return ""
    c = str(c)
    if lang != "ru":
        for a, b in (("Таблица", "表"), ("таблица", "表"), ("раздел", "章节"), ("Раздел", "章节"), ("п.", "п.")):
            c = c.replace(a, b)
    return c


def _values_text(v, lang: str = "zh") -> str:
    """把知识库 values 渲染成人类可读的"要求"片段（作为引用 snippet）。"""
    ru = (lang == "ru")
    sep = "; " if ru else "；"
    if not isinstance(v, dict):
        return str(v)
    if "desc" in v:
        return str(v["desc"])
    if "spec" in v:
        return str(v["spec"])
    if "set" in v:
        return ("Допустимые значения: " if ru else "允许值：") + " / ".join(str(x) for x in (v["set"] or [])[:10])
    if "series" in v:
        return ("Ряд: " if ru else "系列：") + " / ".join(str(x) for x in (v["series"] or [])[:10])
    if "min" in v and "max" in v:
        return f"{v['min']} ~ {v['max']}"
    if "min" in v:
        return f"≥ {v['min']}"
    if "max" in v:
        return f"≤ {v['max']}"
    if "range" in v:
        r = v["range"] or []
        return f"{r[0]} ~ {r[-1]}"
    if "table" in v:
        rows = [r for r in (v["table"] or []) if isinstance(r, dict)]
        if not rows:
            return (f"Таблица ({len(v['table'] or [])} строк)" if ru else f"分档表（{len(v['table'] or [])} 行）")
        head = sep.join(_row_text(r, lang) for r in rows[:2])
        pre = f"Таблица ({len(rows)} строк)" if ru else f"分档表（{len(rows)} 行，节选）"
        more = " …" if len(rows) > 2 else ""
        return f"{pre}：{head}{more}"
    ks = [k for k in v.keys()][:4]
    pre = "По маркам: " if ru else "按牌号分档："
    return pre + sep.join(f"{_col(k, lang)}: {_row_text(v[k], lang) if isinstance(v[k], dict) else v[k]}" for k in ks)


def _snip(d: dict, lang: str) -> str:
    return (d.get("snippet_ru") or d.get("snippet") or "") if lang == "ru" else (d.get("snippet") or "")


# ---------------------------------------------------------------------------
# 2. 索引（chunk + 字段加权 tf）
# ---------------------------------------------------------------------------
_F_W = {"title": 3.0, "meta": 2.0, "body": 1.0}
_IDX = {}


def _mk_chunk(side, std, std_zh, kind, iid, title, clause, test, snippet, body_extra="", title_ru="", snippet_ru="") -> dict:
    tf = {}
    for w, txt in (("title", f"{title} {title_ru}"), ("meta", f"{clause} {test} {std} {std_zh}"), ("body", f"{snippet} {body_extra}")):
        for t in _tok(txt):
            tf[t] = tf.get(t, 0.0) + _F_W[w]
    return {"side": side, "std": std, "std_zh": std_zh, "kind": kind, "iid": iid,
            "title": title, "title_ru": title_ru or title, "clause": clause, "test": test, "snippet": snippet, "snippet_ru": snippet_ru or snippet,
            "tf": tf, "len": sum(tf.values()) or 1.0}


def _ttl(d: dict, lang: str) -> str:
    """按语言取标题（俄语优先 name_ru）。"""
    return (d.get("title_ru") or d.get("title")) if lang == "ru" else (d.get("title") or d.get("title_ru"))


def build_index(kb) -> dict:
    chunks = []
    _cnmap = (kb.get("cn_ind_ru") or {}).get("map", {}) or {}
    _NAME_RU.clear()
    # 常见问题库（FAQ）：整条问答入索引；命中后可直接作答（含"本 Agent 不覆盖"的如实说明）
    for it in (kb.get("faq") or {}).get("items", []) or []:
        c = _mk_chunk("faq", it.get("cat") or "FAQ", it.get("cat") or "", "faq", it.get("id"),
                      it.get("q_zh") or it.get("id"), "FAQ", "",
                      (it.get("a_zh") or "")[:500], it.get("kw") or "",
                      title_ru=it.get("q_ru") or it.get("q_zh") or it.get("id"),
                      snippet_ru=(it.get("a_ru") or "")[:500])
        c["faq_zh"] = it.get("a_zh") or ""
        c["faq_ru"] = it.get("a_ru") or it.get("a_zh") or ""
        c["scope"] = it.get("scope") or "covered"
        c["faq_refs"] = it.get("refs") or []
        c["faq_cat"] = it.get("cat") or ""
        chunks.append(c)
    for side in ("cn", "ru"):
        # 技术法规
        for r in kb.get(side, {}).get("regulations", []) or []:
            snip = (r.get("key_points_zh") or "")[:400]
            chunks.append(_mk_chunk(side, r.get("id"), r.get("name_zh"), "regulation",
                                    r.get("id"), r.get("name_zh") or r.get("id"), "技术法规", "",
                                    snip, str(r.get("category_tables_zh") or ""),
                                    title_ru=r.get("name_ru") or r.get("id")))
        # 标准 + 指标
        for s in kb.get(side, {}).get("standards", []):
            chunks.append(_mk_chunk(side, s.get("id"), s.get("name_zh"), "standard", s.get("id"),
                                    s.get("name_zh") or s.get("id"), "标准适用范围", "",
                                    (s.get("scope_zh") or "")[:300], str(s.get("applicability_note") or ""),
                                    title_ru=s.get("name_ru") or s.get("name_zh") or s.get("id")))
            for ind in s.get("indicators", []):
                _iid = ind.get("id")
                _tru = ind.get("name_ru") or ind.get("name_zh") or _iid
                if side == "cn":
                    _tru = _cnmap.get(_iid) or _tru
                _NAME_RU[_iid] = _tru
                chunks.append(_mk_chunk(side, s.get("id"), s.get("name_zh"), "indicator",
                                        _iid, ind.get("name_zh") or ind.get("name_ru") or _iid,
                                        ind.get("clause") or "", ind.get("test_method") or "",
                                        _values_text(ind.get("values")), str(ind.get("category") or ""),
                                        title_ru=_tru,
                                        snippet_ru=_values_text(ind.get("values"), "ru")))
    df = {}
    for c in chunks:
        for t in c["tf"]:
            df[t] = df.get(t, 0) + 1
    N = len(chunks) or 1
    avg = sum(c["len"] for c in chunks) / N
    return {"chunks": chunks, "df": df, "avg": avg, "N": N}


def _index(kb) -> dict:
    if not _IDX:
        _IDX.update(build_index(kb))
    return _IDX


# ---------------------------------------------------------------------------
# 3. BM25 检索 + 重排
# ---------------------------------------------------------------------------
_K1, _B = 1.5, 0.75


def _bm25(qtoks, c, idx) -> float:
    s = 0.0
    for t in qtoks:
        f = c["tf"].get(t, 0.0)
        if not f:
            continue
        idf = math.log(1 + (idx["N"] - idx["df"].get(t, 0) + 0.5) / (idx["df"].get(t, 0) + 0.5))
        s += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * c["len"] / idx["avg"]))
    return s


def retrieve(query: str, kb, k: int = 6, side: Optional[str] = None) -> list:
    idx = _index(kb)
    qtoks = _tok(query)
    if not qtoks:
        return []
    qset = set(qtoks)
    std_nums = set(re.findall(r"(?<!\d)(\d{2,5})(?!\d)", str(query)))
    qn = re.sub(r"[\s\-/．。，,]+", "", str(query)).lower()
    scored = []
    for c in idx["chunks"]:
        if side and c["side"] != side:
            continue
        sc = _bm25(qset, c, idx)
        if sc <= 0:
            continue
        # ---- rerank ----
        if std_nums and any(n in str(c["std"]) for n in std_nums):
            sc += 6.0
        title_n = re.sub(r"[\s\-/．。，,（）()]+", "", str(c["title"])).lower()
        if len(title_n) >= 2 and (title_n in qn or (len(qn) >= 2 and qn in title_n)):
            sc += 3.0
        if qset and qset.issubset(set(c["tf"].keys())):
            sc += 1.0
        scored.append((sc, c))
    scored.sort(key=lambda x: (-x[0], str(x[1]["iid"])))
    out = []
    for sc, c in scored[:k]:
        d = dict(c)
        d.pop("tf", None)
        d["score"] = round(sc, 2)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# 4. 参数抽取 / 意图路由
# ---------------------------------------------------------------------------
_CN_GRADES = ["Q460D", "Q420D", "Q390D", "Q355B", "Q345D", "Q275B", "Q275A", "Q235B", "Q235A",
              "Q215B", "Q215A", "Q195", "06Cr19Ni10N", "06Cr19Ni10", "022Cr19Ni10",
              "06Cr17Ni12Mo2", "022Cr17Ni12Mo2", "06Cr18Ni11Ti", "HRB500", "HRB400"]
_RU_GRADES = ["12Х18Н10Т", "08Х18Н10Т", "08Х18Н10", "08Х17Н15М3Т", "10Х17Н13М2Т", "17Х18Н9",
              "Ст1сп", "Ст2сп", "Ст3сп", "Ст3пс", "Ст3кп", "Ст4сп", "09Г2С", "17Г1С"]


def extract_params(text: str) -> dict:
    t = str(text or "")
    p = {}
    def _num(pat, key, flags=0):
        m = re.search(pat, t, flags)
        if m:
            try:
                p[key] = float(m.group(1))
            except Exception:
                pass
    _num(r"(?:DN|Ду|公称(?:通径|口径))\s*[:：]?\s*(\d{1,4})", "dn_mm", re.I)
    _num(r"(?:外径|OD|Φ|φ)\s*[:：]?\s*([\d.]+)", "od_mm", re.I)
    _num(r"(?:壁厚|厚度)\s*[:：]?\s*([\d.]+)\s*(?:mm|毫米)?", "wall_thickness_mm")
    _num(r"(?:长度)\s*[:：]?\s*([\d.]+)\s*(?:m|米)?", "length_m")
    _num(r"(?:工作压力|设计压力|压力)\s*[:：]?\s*([\d.]+)\s*(?:MPa|兆帕|МПа)?", "working_pressure_mpa", re.I)
    if "working_pressure_mpa" in p:
        p["design_pressure_mpa"] = p["working_pressure_mpa"]
    _num(r"(?:水压|试验压力|液压)\s*[:：]?\s*([\d.]+)\s*(?:MPa|兆帕|МПа)?", "hydrostatic_test_mpa", re.I)
    _num(r"(?:抗拉(?:强度)?|Rm|σв)\s*[:：]?\s*([\d.]+)", "rm_mpa", re.I)
    _num(r"(?:屈服(?:强度)?|ReL|σт|Rp0\.?2)\s*[:：]?\s*([\d.]+)", "re_mpa", re.I)
    _num(r"(?:伸长率|断后伸长率|δ5)\s*[:：]?\s*([\d.]+)\s*%?", "a_pct")
    _num(r"(?:镀锌层|锌层)\s*[:：]?\s*([\d.]+)", "zinc_coat_g_m2")
    for g in _CN_GRADES:
        if g in t:
            p["material_grade_cn"] = g
            break
    for g in _RU_GRADES:
        if g in t:
            p["material_grade_ru"] = g
            break
    if "镀锌" in t or "黑管" in t:
        p["_hint_type"] = "低压流体输送用焊接钢管(镀锌/黑管)"
    elif "涂覆" in t:
        p["_hint_type"] = "消防涂覆钢管"
    elif "不锈钢" in t:
        p["_hint_type"] = "不锈钢无缝钢管" if "焊" not in t else "不锈钢焊接钢管"
    elif "直缝" in t:
        p["_hint_type"] = "直缝焊接钢管"
    elif "无缝" in t:
        p["_hint_type"] = "无缝钢管"
    return p


def _infer_type(p: dict) -> str:
    if p.get("_hint_type"):
        return p["_hint_type"]
    g = p.get("material_grade_cn", "")
    if g.startswith(("06Cr", "022Cr")):
        return "不锈钢无缝钢管"
    return "低压流体输送用焊接钢管(镀锌/黑管)"


def _enough(p: dict) -> bool:
    has_size = bool(p.get("dn_mm") or (p.get("od_mm") and p.get("wall_thickness_mm")))
    has_mat = bool(p.get("material_grade_cn") or p.get("material_grade_ru"))
    return has_size and has_mat


def _build_product(p: dict) -> dict:
    typ = _infer_type(p)
    dn = p.get("dn_mm")
    od = p.get("od_mm") or (114.0 if dn == 100 else None)
    props = {k: p.get(k) for k in ("rm_mpa", "re_mpa", "a_pct", "hydrostatic_test_mpa",
                                    "zinc_coat_g_m2", "rp02_mpa")}
    props["chemical"] = {k: None for k in ("C", "Si", "Mn", "P", "S", "Cr", "Ni", "Mo")}
    return {"model": "对话输入", "type": typ,
            "material_grade_cn": p.get("material_grade_cn", ""),
            "material_grade_ru": p.get("material_grade_ru", ""),
            "dn_mm": dn, "od_mm": od, "wall_thickness_mm": p.get("wall_thickness_mm"),
            "length_m": p.get("length_m"), "working_pressure_mpa": p.get("working_pressure_mpa"),
            "design_pressure_mpa": p.get("design_pressure_mpa"),
            "medium_group": 2, "medium_phase": "liquid", "intended_use": "消防给水/喷淋",
            "connection": "焊接", "threaded": False, "properties": props}


def _intent(text: str, params: dict, params_cur: dict = None) -> str:
    """意图路由：missing / capability / types / std_detail / list / evaluate / regulation / clause / help。

    判定规则（踩过两次坑后定的）：
      * 元问题（缺数据/能力/类型/指标清单/知识库清单）**优先于 evaluate**；
      * 但 evaluate 只看**当前这句话是否带参数**（params_cur），不能因为"上下文里有参数"
        就把「ГОСТ 3262 壁厚要求」这种条款查询也判成 evaluate；
      * 多轮改参数（如"那壁厚4.0呢"）当前句带参数 → 仍走 evaluate，用合并后的参数。
    """
    t = str(text or "")
    _cur = extract_params(t) if params_cur is None else params_cur
    # 元问题（顺序敏感：更具体的先判，避免被"有哪些/支持哪些"泛匹配吃掉）
    # ① 能力询问
    if re.search(r"你能(?:做|干|帮)(?:什么|啥)?|你会(?:做)?什么|有什么功能|有哪些功能|能帮(?:我)?(?:做)?什么|"
                 r"怎么用|如何使用|使用说明|что ты (?:умеешь|можешь)|какие (?:у тебя )?функции", t, re.I):
        return "capability"
    # ② 支持的产品类型 / 牌号
    if re.search(r"(?:支持|能评|可以评|覆盖)(?:哪些|什么)?(?:管材|产品|类型|牌号|材质|钢管)|"
                 r"哪些(?:管材|产品类型|牌号|材质)|有哪些(?:管材|产品类型|牌号|材质)|"
                 r"какие (?:типы|марки|трубы)", t, re.I):
        return "types"
    # ③ 缺哪些数据 / 未评项清单（需结合多轮上下文）
    if re.search(r"缺(?:哪些|什么|少)|还缺|哪些(?:数据|没评|未评)|未评项|缺数据|需要补(?:充)?(?:哪些|什么)|"
                 r"не хватает|каких данных|что дополнить", t, re.I):
        return "missing"
    # ④ 某标准包含哪些指标
    if re.search(r"(?:有哪些|包含哪些|都有哪些|有什么|哪些|какие)\s*(?:指标|项目|检查项|条款|показател)", t, re.I):
        return "std_detail"
    # ⑤ 知识库清单（"你有哪些法规/标准""知识库里有什么"）—— 必须在"法规"关键词之前判定
    if re.search(r"知识库|有哪些|哪些标准|哪些法规|收录|覆盖(?:哪些|了)|包含(?:哪些|什么)|支持哪些|清单|列表|"
                 r"база знаний|какие стандарт|какие регламент|что (?:есть|входит)", t, re.I):
        return "list"
    # 报告请求：当前句通常不带参数（"生成完整集体评价报告"），用上下文参数出报告
    if re.search(r"报告|完整|详细|全文|导出|отч[её]т|полный", t, re.I):
        return "evaluate" if _enough(params) else "report_ask"
    # —— 元问题判完，才轮到"当前句带参数就评价" ——
    if _cur and _enough(params):
        return "evaluate"
    if re.search(r"032|043|技术法规|强制认证|要不要认证|适用(范围)?|属于|EAC|EAC认证|сертифик", t, re.I):
        return "regulation"
    if re.search(r"要求|多少|是什么|怎么|标准|条款|规定|限值|允许|ГОСТ|GOST|GB|СП|壁厚|水压|镀锌|成分|力学|尺寸|偏差", t, re.I):
        return "clause"
    if len(_tok(t)) <= 1:
        return "help"
    return "clause"


# ---------------------------------------------------------------------------
# 5. 回答组织（结构化 + 引用 + 追问）
# ---------------------------------------------------------------------------
def _ref_item(d: dict, lang: str = "zh") -> dict:
    return {"side": d["side"], "std": d["std"], "name": _ttl(d, lang), "clause": d.get("clause") or "",
            "test": d.get("test") or "", "snippet": d.get("snippet") or "", "score": d.get("score", 0.0)}


def _cite_lines(refs: list, lang: str = "zh") -> list:
    ru = (lang == "ru")
    out = ["", ("**📚 Источники (поиск по базе · по релевантности):**" if ru else "**📚 来源（知识库检索 · 按相关度）：**")]
    for i, r in enumerate(refs[:5], 1):
        flag = _flag(r["side"], lang)
        head = f"{i}. {flag} **{r['std']}**｜{_ttl(r, lang)}"
        if r.get("clause"):
            head += (f" · п. {r['clause']}" if ru else f" · 条款 {_clause_text(r['clause'], lang)}")
        out.append(head)
        sn = _snip(r, lang)
        if sn:
            out.append(f"   > {sn[:160]}")
        if r.get("test"):
            out.append(("   > Метод: " if ru else "   > 测试方法：") + str(r["test"]))
    return out


def _followups(intent: str, refs: list, params: dict, lang: str = "zh") -> list:
    """追问建议（跟随界面语言）。"""
    P = _ph(lang)
    if intent == "evaluate":
        return list(P["fu_eval"])
    if intent == "regulation":
        return list(P["fu_reg"])
    std = refs[0]["std"] if refs else ""
    if std:
        return [f"{std} {x}" for x in P["fu_std_suffix"]]
    return list(P["fu_default"])


# 界面/回答用语（zh / ru）
PH = {
    "zh": {
        "head": "我按你给的参数看了一遍 👇", "cn": "中国这边", "ru": "俄罗斯这边",
        "pass_all": "能评的 {t} 项都**通过了**", "na_more": "但还有 {n} 项缺数据（成分、力学、实测偏差等），覆盖度只有 {c}%，所以**先不给你 A~D 的结论**——补上缺的就能定级。",
        "fail_n": "能评的 {t} 项里有 **{f} 项没过**", "na_tail": "另有 {n} 项缺数据；覆盖度 {c}%，暂时**不定级**。",
        "grade": "评级 **{g}** ——", "allpass": "能评的 {t} 项**全部通过**", "somefail": "能评的 {t} 项里有 **{f} 项没过**",
        "cov": "（覆盖度 {c}%）", "fails": "⚠️ **没过的项**：", "edges": "🟠 **其中很接近合格线的**：{e} —— 就差一点点，建议优先复核。",
        "path": "**认证怎么走**", "notin032": "这根管**不属 ТР ТС 032/2013**（承压设备）范围；",
        "fire043": "但作为消防系统管道，须按 **ТР ЕАЭС 043/2017 强制认证**。",
        "in032": "按 ТР ТС 032/2013 属 **Категория {c}** → {r}；", "also043": "同时作为消防管按 **ТР ЕАЭС 043/2017 强制认证**。",
        "askmore": "想让我给出确定的 A~D，把这些补上就行：**抗拉强度、屈服强度、伸长率、化学成分(C/Si/Mn/P/S)、实测外径/壁厚、压扁/弯曲结果、水压**。",
        "basis": "依据：", "found": "查到了 —— ", "related": "相关的还有：", "testmethod": "测试方法：",
        "notfound": "没直接查到对应条款 —— 换个说法，或者带上标准号（如 `ГОСТ 3262 壁厚`）我再找找。",
        "askjudge": "想让我直接判你这根管达不达标，给我 **DN + 壁厚 + 牌号**（能再给水压、抗拉就更好）。",
        "reg_intro": "简单说，这两个法规**看的东西不一样**：",
        "reg_032": "**ТР ТС 032/2013** 看「**超压风险**」——按 P×DN 查表定类别。**消防水管**（水、第 2 组介质、DN≤200）通常**不在它范围里**。",
        "reg_043": "**ТР ЕАЭС 043/2017** 看「**用途与材料**」——作为**消防系统元件**申报时，**必须强制认证（сертификация）**。",
        "reg_both": "两者原理不同、**可能同时适用**；而且 **EAC 证书必须由欧亚经济联盟境内主体持有**（中国企业得找俄/吉/哈的委托方办）。",
        "help": "你好 👋 我是**中俄消防管材合规助手**，直接问我话就行：",
        "help1": "- **要我评价管子** → 给参数，例如 `DN100 镀锌管 Q235B 壁厚4.5 水压2.5`",
        "help2": "- **问标准要求** → 例如 `ГОСТ 3262 壁厚要求`、`GB/T 3091 镀锌层`",
        "help3": "- **问认证/法规** → 例如 `消防管要不要 EAC 认证`、`ТР ТС 032 适用范围`",
        "helptail": "下方有示例，点一下就能试；也可以直接打字。",
        "list_head": "我的知识库里收录了这些标准和法规 👇",
        "list_cn": "中国标准", "list_ru": "俄罗斯标准", "list_reg": "欧亚经济联盟技术法规",
        "list_tail": "想查其中某条，直接报标准号即可（如 `ГОСТ 3262 壁厚`、`GB/T 3091 镀锌层`）；想评价管子，给我 DN+壁厚+牌号。",
        "cap_head": "我能做这几件事 👇",
        "cap_items": [
            "- **评价管子**：给参数（DN、壁厚、牌号、水压、抗拉…）→ 出中俄合规初筛 + EAC 认证路径",
            "- **查标准条款**：如 `ГОСТ 3262 壁厚要求`、`GB/T 3091 镀锌层`",
            "- **查技术法规**：如 `ТР ТС 032 适用范围`、`消防管要不要 EAC`",
            "- **看知识库清单**：如 `你有哪些标准`",
            "- **看某标准有哪些指标**：如 `GB/T 3091 里有哪些指标`",
            "- **看支持的产品类型/牌号**：如 `支持哪些管材类型`",
        ],
        "cap_tail": "界面可切**中文/俄语**（右上角按钮）。",
        "types_head": "我目前能评价这些产品类型 👇",
        "types_cn": "可识别的中牌号：", "types_ru": "可识别的俄牌号：",
        "types_tail": "不在列表里的类型/牌号，也可以直接说，我会按最接近的判定（并提示不支持的部分）。",
        "std_head": "**{std}**　{nm} —— 共收录 **{n}** 项指标：",
        "std_ask": "请告诉我是哪个标准，例如 `GB/T 3091 里有哪些指标`、`ГОСТ 3262 有哪些指标`。",
        "fu_eval": ["该管按 ТР ЕАЭС 043 要不要强制认证？", "生成完整集体评价报告", "你有哪些标准"],
        "fu_reg": ["ТР ТС 032 管道类别怎么判？", "ТР ЕАЭС 043 适用哪些消防器材？", "DN100 镀锌管 Q235B 壁厚4.5"],
        "fu_std_suffix": ["水压试验压力", "化学成分要求", "尺寸允许偏差"],
        "fu_default": ["GB/T 3091 镀锌层要求", "ГОСТ 3262 壁厚要求", "ТР ТС 032 适用范围"],
        "fu_capability": ["DN100 镀锌管 Q235B 壁厚4.5 水压2.5", "你有哪些标准", "支持哪些管材类型"],
        "fu_types": ["DN100 镀锌管 Q235B 壁厚4.5", "你有哪些标准", "你能做什么"],
        "fu_std": ["你有哪些标准", "支持哪些管材类型", "GB/T 3091 里有哪些指标"],
        "fu_list": ["GB/T 3091 镀锌层要求", "ГОСТ 3262 壁厚要求", "ТР ТС 032 适用范围"],
        "fu_faq": ["你有哪些标准", "支持哪些管材类型", "ГОСТ 3262 壁厚要求"],
        "miss_head": "目前有 **{n}** 项没评（缺数据或需判定），清单如下 👇",
        "miss_none": "当前参数下没有未评项，已全部评完。",
        "miss_ask": "我这边没有可评价的产品参数 —— 先给我 **DN + 壁厚 + 牌号**，我再告诉你还缺哪些数据。",
        "fu_missing": ["生成完整集体评价报告", "你有哪些标准", "支持哪些管材类型"],
        "ok_list": "✅ 通过", "na_list": "◻️ 未评（缺数据/需判定）", "need_txt": "需补",
        "na_na": "（不适用）", "na_opt": "（可选/非强制）",
        "rep_ask": "生成完整报告需要产品参数哦 —— 先给我 **DN + 壁厚 + 牌号**（例如 `DN100 镀锌管 Q235B 壁厚4.5 水压2.5`）我就能出；也可以直接去 **表单版** 填完整参数。",
        "fu_rep_ask": ["DN100 镀锌管 Q235B 壁厚4.5 水压2.5", "你有哪些标准", "你能做什么"],
    },
    "ru": {
        "head": "Я просмотрел указанные параметры 👇", "cn": "Китай", "ru": "Россия",
        "pass_all": "все {t} оценённых пунктов **пройдены**", "na_more": "но по {n} пунктам нет данных (химсостав, механика, фактические размеры), охват всего {c}%, поэтому **степень A~D пока не присваивается** — дополните данные.",
        "fail_n": "из {t} оценённых пунктов **{f} не пройдено**", "na_tail": "ещё {n} пунктов без данных; охват {c}%, степень **не присвоена**.",
        "grade": "степень **{g}** —", "allpass": "все {t} оценённых пунктов **пройдены**", "somefail": "из {t} оценённых пунктов **{f} не пройдено**",
        "cov": "(охват {c}%)", "fails": "⚠️ **Не пройдено**: ", "edges": "🟠 **Близко к границе**: {e} — совсем немного, проверьте в первую очередь.",
        "path": "**Путь сертификации**", "notin032": "труба **не входит в область ТР ТС 032/2013** (оборудование под давлением); ",
        "fire043": "но как элемент системы пожаротушения требует **обязательной сертификации по ТР ЕАЭС 043/2017**.",
        "in032": "по ТР ТС 032/2013 — **Категория {c}** → {r}; ", "also043": "также обязательная сертификация по **ТР ЕАЭС 043/2017**.",
        "askmore": "Для точной степени A~D дополните: **σв, σт, δ5, химсостав (C/Si/Mn/P/S), фактические диаметр/стенку, сплющивание/изгиб, гидроиспытание**.",
        "basis": "Основание: ", "found": "Найдено — ", "related": "Также относится: ", "testmethod": "Метод испытания: ",
        "notfound": "Прямого пункта не найдено — переформулируйте или укажите номер стандарта (напр. `ГОСТ 3262 стенка`).",
        "askjudge": "Чтобы я оценил вашу трубу, дайте **DN + толщину стенки + марку стали** (можно ещё давление и σв).",
        "reg_intro": "Коротко: эти два регламента регулируют **разное**:",
        "reg_032": "**ТР ТС 032/2013** — «**риск избыточного давления**»: категория по P×DN. Водопроводы пожаротушения (вода, группа 2, DN≤200) обычно **вне области**. ",
        "reg_043": "**ТР ЕАЭС 043/2017** — «**назначение и материал**»: как элемент системы пожаротушения требует **обязательной сертификации**. ",
        "reg_both": "Принципы разные и **возможно одновременное применение**; **сертификат EAC должен держать резидент ЕАЭС** (китайской компании нужен партнёр в РФ/КГ/КЗ).",
        "help": "Здравствуйте 👋 Я **ИИ-консультант по соответствию стальных труб КНР/РФ**:",
        "help1": "- **Оценить трубу** → дайте параметры, напр. `DN100 оцинк. Q235B стенка 4.5 давление 2.5`",
        "help2": "- **Требования стандартов** → напр. `ГОСТ 3262 толщина стенки`, `GB/T 3091 цинковое покрытие`",
        "help3": "- **Сертификация / регламенты** → напр. `нужен ли EAC`, `ТР ТС 032 область применения`",
        "helptail": "Ниже есть примеры — нажмите, чтобы попробовать, или напишите свой вопрос.",
        "list_head": "В моей базе знаний есть следующие стандарты и регламенты 👇",
        "list_cn": "Стандарты КНР", "list_ru": "Стандарты РФ", "list_reg": "Технические регламенты ЕАЭС",
        "list_tail": "Чтобы посмотреть конкретный пункт — укажите номер стандарта (напр. `ГОСТ 3262 стенка`); для оценки трубы дайте DN + стенку + марку.",
        "cap_head": "Я умею следующее 👇",
        "cap_items": [
            "- **Оценить трубу**: дайте параметры (DN, стенка, марка, давление, σв…) → соответствие КНР/РФ + путь сертификации EAC",
            "- **Найти пункт стандарта**: напр. `ГОСТ 3262 толщина стенки`",
            "- **Регламенты**: напр. `ТР ТС 032 область применения`, `нужен ли EAC`",
            "- **Список базы знаний**: напр. `какие стандарты в базе`",
            "- **Показатели стандарта**: напр. `ГОСТ 3262 какие показатели`",
            "- **Поддерживаемые типы/марки**: напр. `какие типы труб`",
        ],
        "cap_tail": "Интерфейс переключается **中文 / Русский** (кнопка справа вверху).",
        "types_head": "Я могу оценивать следующие типы труб 👇",
        "types_cn": "Распознаваемые марки КНР: ", "types_ru": "Распознаваемые марки РФ: ",
        "types_tail": "Если типа/марки нет в списке — всё равно напишите, оценю по ближайшему и укажу ограничения.",
        "std_head": "**{std}**　{nm} — всего **{n}** показателей:",
        "std_ask": "Укажите стандарт, напр. `ГОСТ 3262 какие показатели`, `GB/T 3091 какие показатели`.",
        "fu_eval": ["Нужна ли сертификация по ТР ЕАЭС 043?", "Показать полный отчёт", "какие стандарты в базе"],
        "fu_reg": ["Как определить категорию по ТР ТС 032?", "Что относится к ТР ЕАЭС 043?", "DN100 оцинк. Q235B стенка 4.5"],
        "fu_std_suffix": ["гидроиспытание", "химсостав", "допуски"],
        "fu_default": ["GB/T 3091 цинковое покрытие", "ГОСТ 3262 толщина стенки", "ТР ТС 032 область применения"],
        "fu_capability": ["DN100 оцинк. Q235B стенка 4.5 давление 2.5", "какие стандарты в базе", "какие типы труб"],
        "fu_types": ["DN100 оцинк. Q235B стенка 4.5", "какие стандарты в базе", "что ты умеешь"],
        "fu_std": ["какие стандарты в базе", "какие типы труб", "ГОСТ 3262 какие показатели"],
        "fu_list": ["ГОСТ 3262 толщина стенки", "GB/T 3091 цинковое покрытие", "ТР ТС 032 область применения"],
        "fu_faq": ["какие стандарты в базе", "какие типы труб", "ГОСТ 3262 толщина стенки"],
        "miss_head": "Сейчас **{n}** показателей не оценено (нет данных или нужно решение) 👇",
        "miss_none": "При текущих данных неоценённых показателей нет.",
        "miss_ask": "Нет параметров для оценки — дайте **DN + стенку + марку**, и я скажу, каких данных не хватает.",
        "fu_missing": ["Показать полный отчёт", "какие стандарты в базе", "какие типы труб"],
        "ok_list": "✅ пройдено", "na_list": "◻️ не оценено (нет данных)", "need_txt": "нужно",
        "na_na": " (не применимо)", "na_opt": " (опционально)",
        "rep_ask": "Для полного отчёта нужны параметры трубы — дайте **DN + стенку + марку** (напр. `DN100 оцинк. Q235B стенка 4.5 давление 2.5`) или заполните **форму**.",
        "fu_rep_ask": ["DN100 оцинк. Q235B стенка 4.5 давление 2.5", "какие стандарты в базе", "что ты умеешь"],
    },
}


def _ph(lang):
    return PH.get(lang) or PH["zh"]


# 产品类型 → 俄文
TYPE_RU = {
    "低压流体输送用焊接钢管(镀锌/黑管)": "Электросварные трубы (оцинк./чёрные)",
    "消防涂覆钢管": "Трубы с покрытием (пожарные)",
    "不锈钢无缝钢管": "Бесшовные нержавеющие трубы",
    "不锈钢焊接钢管": "Сварные нержавеющие трубы",
    "无缝钢管": "Бесшовные стальные трубы",
    "冷拔无缝钢管": "Холоднодеформированные бесшовные трубы",
    "直缝焊接钢管": "Прямошовные электросварные трубы",
    "干线管道焊接钢管": "Магистральные сварные трубы",
}


def _ru_punct(s: str) -> str:
    """把俄语回答里混入的中文标点统一成西文标点（俄语更自然）。"""
    for a, b in (("：", ": "), ("，", ", "), ("。", ". "), ("（", " ("), ("）", ") "),
                 ("、", ", "), ("；", "; "), ("！", "! "), ("？", "? "), ("｜", " | "), ("～", "~")):
        s = s.replace(a, b)
    # 收尾清理：避免出现 " :" / " ;" / " ." 之类的多余空格
    for a, b in ((" :", ":"), (" ;", ";"), (" ,", ","), (" .", "."), (" !", "!"), (" ?", "?"),
                 (") **", ")**")):
        s = s.replace(a, b)
    return s


def answer(message: str, kb=None, history=None, lang: str = "zh") -> dict:
    """RAG 主入口。history: [{'role':'user'|'bot','content':str}] —— 支持多轮追问；lang: zh/ru。"""
    kb = kb or E.load_knowledge_base()
    P = _ph(lang)
    # 多轮承接：把最近一条用户消息一起抽参数（"那316L呢"可继承尺寸）
    prev_user = ""
    for h in reversed(history or []):
        if h.get("role") == "user":
            prev_user = h.get("content", "")
            break
    # 多轮承接：先取上文参数，再用当前消息**覆盖**（使"那壁厚4.0呢"能改掉上文的4.5）
    params = extract_params(prev_user)
    params.update(extract_params(message))
    intent = _intent(message, params, extract_params(message))
    refs = retrieve(message, kb, 6)
    L = []

    if intent == "evaluate":
        product = _build_product(params)
        res = E.evaluate(product, kb)
        idx, ov = res["index"], res["overall"]
        cn, ru = idx["cn"], idx["ru"]
        def _side_txt(si, label):
            g, p, f_, na = si["grade"], si["passed"], si["failed"], si["na"]
            tot = p + f_
            res = "全部通过" if f_ == 0 else f"{f_} 项未通过"
            covp = round((si["coverage"] or 0) * 100)
            if g == "NA":
                return (f"- {label}：**未定级**（已评 {tot} 项{res}；覆盖度 {covp}%、未评 {na} 项偏多，"
                        f"故不给出 A~D 定级）")
            return f"- {label}：**{g}**（已评 {tot} 项{res}；覆盖度 {covp}%）"

        import llm_report as _LR
        fails, edges = [], []
        for s in res["cn_results"] + res["ru_results"]:
            for it in s["items"]:
                if it["verdict"] == "fail":
                    nm = _item_name(it, lang)
                    fails.append(nm)
                    g = _LR._edge_gap(it.get("detail", ""))
                    if g is not None and g <= 0.05:
                        edges.append(f"{nm}（差 {round(g*100,1)}%）")
        _sep = "、" if lang == "zh" else "; "
        _mat = product.get("material_grade_cn") or product.get("material_grade_ru") or "—"
        _ptype = (TYPE_RU.get(product["type"], product["type"]) if lang == "ru" else product["type"])
        L.append(f"{P['head']}（{_ptype}｜{_mat}｜DN {product.get('dn_mm') or '—'}"
                 f"｜OD {product.get('od_mm') or '—'}｜t {product.get('wall_thickness_mm') or '—'}）")
        L.append("")

        def _say(si, who):
            tot = si["passed"] + si["failed"]
            cov = round((si["coverage"] or 0) * 100)
            if si["grade"] == "NA":
                if si["failed"] == 0:
                    return f"**{who}**：" + P["pass_all"].format(t=tot) + "，" + P["na_more"].format(n=si["na"], c=cov)
                return f"**{who}**：" + P["fail_n"].format(t=tot, f=si["failed"]) + "，" + P["na_tail"].format(n=si["na"], c=cov)
            if si["failed"] == 0:
                return f"**{who}**：" + P["grade"].format(g=si["grade"]) + " " + P["allpass"].format(t=tot) + P["cov"].format(c=cov) + "。"
            return f"**{who}**：" + P["grade"].format(g=si["grade"]) + " " + P["somefail"].format(t=tot, f=si["failed"]) + P["cov"].format(c=cov) + "。"

        def _items(res_key, verdict):
            out = []
            for s in res.get(res_key, []):
                for it in s.get("items", []):
                    if it.get("verdict") == verdict:
                        out.append(it)
            return out

        def _detail(res_key):
            """逐项列出：通过 / 未通过 / 缺数据（含"缺什么参数"）。"""
            oks = _items(res_key, "pass")
            bad = _items(res_key, "fail")
            nas = _items(res_key, "na")
            if oks:
                L.append(f"　{P['ok_list']}（{len(oks)}）："
                         + _sep.join(_item_name(it, lang) for it in oks))
            if bad:
                for it in bad:
                    L.append(f"　❌ **{_item_name(it, lang)}**"
                             + (f"：{_edge_short_safe(it.get('detail', ''))}" if it.get("detail") else ""))
            if nas:
                L.append(f"　{P['na_list']}（{len(nas)}）：")
                for it in nas:
                    _nm = _item_name(it, lang)
                    _rs = _na_reason(it.get("detail", ""))
                    _h = _need_hint(it, lang)
                    # 俄语界面保留原单位（避免 "МПа, MPa" 重复）；中文界面转成 μm/mm 等
                    _u = (it.get("unit") or "").strip() if lang == "ru" else _unit_zh(it.get("unit"))
                    if _rs.startswith("不适用"):
                        _tail = P["na_na"]
                    elif _rs.startswith("可选") or "可选/按协议" in _rs:
                        _tail = P["na_opt"]
                    elif _h:
                        _unit = f"，{_u}" if _u and _u != "-" and _u not in _h else ""
                        _tail = f"（{P['need_txt']} {_h}{_unit}）"
                    else:
                        _tail = f"（{P['need_txt']} {_u if _u and _u != '-' else '实测值'}）"
                    L.append(f"　  · {_nm}{_tail}")

        L.append(_say(cn, P["cn"]))
        _detail("cn_results")
        L.append(_say(ru, P["ru"]))
        _detail("ru_results")
        if edges:
            L.append(P["edges"].format(e=_sep.join(edges)))
        L.append("")
        if not res["tr_cu_032"]["in_scope"]:
            L.append(P["path"] + "：" + P["notin032"] + (P["fire043"] if res["tr_eaeu_043"]["applies"] else ""))
        else:
            L.append(P["path"] + "：" + P["in032"].format(c=res["tr_cu_032"]["category"], r=res["tr_cu_032"]["route"])
                     + (P["also043"] if res["tr_eaeu_043"]["applies"] else ""))
        if cn["grade"] == "NA" or ru["grade"] == "NA":
            L.append("")
            L.append(P["askmore"])
        eval_refs = []
        for key, sd in (("cn_results", "cn"), ("ru_results", "ru")):
            for s in res[key]:
                eval_refs.append({"side": sd, "std": s.get("standard"), "title": s.get("name_zh") or s.get("standard"),
                                  "clause": "", "test": "", "snippet": "", "score": 0})
        L.append("")
        L.append(P["basis"] + "；".join(_flag(d["side"], lang) + str(d["std"]) for d in (eval_refs or refs)[:3]) + "。")
        report = None
        if re.search(r"报告|完整|详细|全文|导出|отч[её]т|полный", message, re.I):
            try:
                import llm_report as LR
                report = LR.generate_report(product, res, LR.get_config(), lang)["report"]
            except Exception:
                pass
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)), "refs": [_ref_item(d, lang) for d in (eval_refs or refs)],
                "product": product, "report": report, "followups": _followups(intent, refs, params, lang)}

    if intent == "report_ask":
        L.append(P["rep_ask"])
        return {"intent": intent,
                "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_rep_ask"])}

    if intent == "missing":
        # 缺哪些数据 / 未评项清单：从多轮上下文取回参数，重跑评价，列出 NA 项
        _hist_users = [h.get("content", "") for h in (history or []) if h.get("role") == "user"]
        _merged = {}
        for _m in _hist_users + [message]:
            _merged.update(extract_params(_m))
        if _enough(_merged):
            _prod = _build_product(_merged)
            _res = E.evaluate(_prod, kb)
            _nas = []
            for _key, _side in (("cn_results", P["cn"]), ("ru_results", P["ru"])):
                for _s in _res.get(_key, []):
                    for _it in _s.get("items", []):
                        if _it.get("verdict") == "na":
                            _nas.append((_side, _s.get("standard"), _it))
            if _nas:
                L.append(P["miss_head"].format(n=len(_nas)))
                L.append("")
                for _side, _std, _it in _nas:
                    _nm = _it.get("indicator_zh") or _it.get("indicator_id")
                    L.append(f"- {_side}｜{_std}｜**{_nm}**（{_it.get('indicator_id')}）")
                    _rs = _na_reason(_it.get("detail", ""))
                    if _rs:
                        L.append(f"　✋ {_rs}")
                L.append("")
                L.append(P["askmore"])
            else:
                L.append(P["miss_none"])
        else:
            L.append(P["miss_ask"])
        return {"intent": intent,
                "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_missing"])}

    # FAQ 命中：仅当 FAQ 分数明显高于普通条款检索时才用 FAQ 作答（避免抢答条款查询）
    _faq_hit = None
    if refs and refs[0].get("kind") == "faq":
        _best_other = next((r for r in refs if r.get("kind") != "faq"), None)
        if _best_other is None or refs[0].get("score", 0) >= _best_other.get("score", 0) * 1.15:
            _faq_hit = refs[0]
    if _faq_hit is not None and intent in ("clause", "regulation", "help"):
        d = _faq_hit
        _ans = (d.get("faq_ru") if lang == "ru" else d.get("faq_zh")) or d.get("faq_zh") or ""
        L.append(_ans)
        _fr = d.get("faq_refs") or []
        if _fr:
            L.append("")
            L.append(P["basis"] + "；".join(str(x) for x in _fr))
        return {"intent": "faq",
                "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [_ref_item(d, lang)], "product": None, "report": None,
                "followups": list(P["fu_faq"])}

    if intent == "capability":
        L.append(P["cap_head"])
        L.append("")
        L.extend(P["cap_items"])
        L.append("")
        L.append(P["cap_tail"])
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_capability"])}

    if intent == "types":
        L.append(P["types_head"])
        L.append("")
        for _t in E.APPLICABILITY.keys():
            L.append(f"- {TYPE_RU.get(_t, _t) if lang == 'ru' else _t}")
        L.append("")
        L.append(P["types_cn"] + "、".join(_CN_GRADES[:12]) + " …")
        L.append(P["types_ru"] + "、".join(_RU_GRADES[:10]) + " …")
        L.append("")
        L.append(P["types_tail"])
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_types"])}

    if intent == "std_detail":
        _nums = re.findall(r"(?<!\d)(\d{2,5})(?!\d)", str(message))
        _target = None
        for _side in ("cn", "ru"):
            for _s in kb.get(_side, {}).get("standards", []):
                if any(n in str(_s.get("id")) for n in _nums):
                    _target = _s
                    break
            if _target:
                break
        if _target:
            _inds = _target.get("indicators", [])
            _nm = ((_target.get("name_ru") or _target.get("name_zh") or "") if lang == "ru"
                   else (_target.get("name_zh") or _target.get("name_ru") or ""))
            L.append(P["std_head"].format(std=_target.get("id"), nm=_nm, n=len(_inds)))
            L.append("")
            for _ind in _inds:
                _inm = ((_ind.get("name_ru") or _ind.get("name_zh") or _ind.get("id")) if lang == "ru"
                        else (_ind.get("name_zh") or _ind.get("name_ru") or _ind.get("id")))
                _cl = f"　· {_ind.get('clause')}" if _ind.get("clause") else ""
                L.append(f"- {_inm}（{_ind.get('id')}）{_cl}")
        else:
            L.append(P["std_ask"])
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_std"])}

    if intent == "list":
        cn_std = kb.get("cn", {}).get("standards", [])
        ru_std = kb.get("ru", {}).get("standards", [])
        regs = kb.get("ru", {}).get("regulations", [])
        ru_mode = (lang == "ru")
        L.append(P["list_head"])
        L.append("")
        L.append(f"**{P['list_cn']}（{len(cn_std)}）**")
        for s in cn_std:
            L.append(f"- {s.get('id')}　{s.get('name_zh') or s.get('name_ru') or ''}")
        L.append("")
        L.append(f"**{P['list_ru']}（{len(ru_std)}）**")
        for s in ru_std:
            nm = (s.get("name_ru") or s.get("name_zh") or "") if ru_mode else (s.get("name_zh") or s.get("name_ru") or "")
            L.append(f"- {s.get('id')}　{nm}")
        L.append("")
        L.append(f"**{P['list_reg']}（{len(regs)}）**")
        for r in regs:
            nm = (r.get("name_ru") or r.get("name_zh") or "") if ru_mode else (r.get("name_zh") or r.get("name_ru") or "")
            L.append(f"- {r.get('id')}　{nm}")
        L.append("")
        L.append(P["list_tail"])
        return {"intent": intent,
                "reply": (_ru_punct("\n".join(L)) if ru_mode else "\n".join(L)),
                "refs": [], "product": None, "report": None,
                "followups": list(P["fu_list"])}

    if intent == "regulation":
        L.append(P["reg_intro"])
        L.append("")
        L.append(P["reg_032"])
        L.append(P["reg_043"])
        L.append("")
        L.append(P["reg_both"])
        L.append("")
        _regrefs = [d for d in refs if d.get("kind") == "regulation"] or refs
        L.append(P["basis"] + "；".join(_flag(d["side"], lang) + str(d["std"]) for d in _regrefs[:2]) + "。")
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)), "refs": [_ref_item(d, lang) for d in _regrefs],
                "product": None, "report": None, "followups": _followups(intent, refs, params, lang)}

    if intent == "clause":
        if refs:
            top = refs[0]
            L.append(P["found"] + f"**{top['std']}｜{_ttl(top, lang)}**"
                     + (f"（{_clause_text(top['clause'], lang)}）" if top.get("clause") else "") + "：")
            if top.get("snippet"):
                L.append("")
                L.append(f"> {_snip(top, lang)[:200]}")
            # "相关的还有" 只列标准/法规/指标，**不列 FAQ 问答条目**（FAQ 不是条款）
            _nonfaq = [d for d in refs if d.get("kind") != "faq"]
            others = _nonfaq[1:3]
            if others:
                L.append("")
                L.append(P["related"] + "；".join(
                    f"{_flag(d['side'], lang)}{d['std']}｜{_ttl(d, lang)}"
                    + (f"（{_clause_text(d['clause'], lang)}）" if d.get("clause") else "") for d in others) + "。")
            if top.get("test"):
                L.append(P["testmethod"] + f"{top['test']}。")
        else:
            L.append(P["notfound"])
        L.append("")
        L.append(P["askjudge"])
        return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)), "refs": [_ref_item(d, lang) for d in refs],
                "product": None, "report": None, "followups": _followups(intent, refs, params, lang)}

    # help
    L.append(P["help"])
    L.append("")
    L.append(P["help1"])
    L.append(P["help2"])
    L.append(P["help3"])
    L.append("")
    L.append(P["helptail"])
    return {"intent": intent, "reply": (_ru_punct("\n".join(L)) if lang == "ru" else "\n".join(L)), "refs": [_ref_item(d, lang) for d in refs],
            "product": None, "report": None, "followups": _followups(intent, refs, params, lang)}


def chat(message: str, kb=None, history=None, lang: str = "zh") -> dict:
    """兼容旧调用名。"""
    return answer(message, kb, history, lang)


if __name__ == "__main__":
    import sys
    kb = E.load_knowledge_base()
    q = " ".join(sys.argv[1:]) or "DN100 镀锌管 Q235B 壁厚4.5 水压2.5"
    out = answer(q, kb)
    print(out["reply"])
    print("\n追问建议：", " | ".join(out["followups"]))
