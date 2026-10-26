#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IFC 管材语义提取 + 中俄合规分析（零第三方依赖）
================================================
输入：Revit / ArchiCAD / Tekla 等导出的 .ifc（IFC2X3 与 IFC4 均可）
输出：
  1) model     模型概况（项目名、schema、单位、构件统计）
  2) specs     按"管材规格"聚合的参数表（与表单版 product 同构，可直接喂 evaluate.evaluate）
  3) results   每个规格的中俄合规结论（与表单版完全同口径，逐条引用条款）
  4) elements  每个管道构件的 3D 中心线/位置 + 所属规格 + 判定色（供前端可视化标注）

设计原则：
  - 只依赖标准库，便于在最小化 ECS 上部署，不受 Python 版本/whl 制约；
  - 属性提取"广谱收集 + 模糊映射"：把构件上所有 Pset 属性都收进来，再按中/英/俄关键词
    映射到 product 字段，任何外部导出器的命名差异都不会导致整条链失败；
  - 几何提取失败不影响合规结论（几何只服务可视化，属性才是判定依据）。

用法：
    python engine/ifc_pipe.py model.ifc out.json
"""
import json
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import evaluate as E  # noqa: E402


# ===========================================================================
# 1. IFC 字符串与词法
# ===========================================================================
_RE_X2 = re.compile(r'\\X2\\((?:[0-9A-Fa-f]{4})*)\\X0\\')
_RE_X4 = re.compile(r'\\X4\\((?:[0-9A-Fa-f]{8})*)\\X0\\')
_RE_S = re.compile(r'\\S\\(.)')
_RE_X = re.compile(r'\\X\\([0-9A-Fa-f]{2})')


def decode_ifc_str(s):
    """解码 IFC 字符串转义：\\X2\\..\\X0\\ (UTF-16BE)、\\X4\\..\\X0\\、\\S\\x、'' 等。"""
    if not s:
        return s
    if '\\' not in s:
        return s.replace("''", "'")

    def _x2(m):
        try:
            return bytes.fromhex(m.group(1)).decode('utf-16-be', 'replace')
        except Exception:
            return ''

    def _x4(m):
        try:
            return bytes.fromhex(m.group(1)).decode('utf-32-be', 'replace')
        except Exception:
            return ''

    s = _RE_X2.sub(_x2, s)
    s = _RE_X4.sub(_x4, s)
    s = _RE_S.sub(lambda m: chr(ord(m.group(1)) + 128), s)
    s = _RE_X.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace('\\\\', '\\')
    return s.replace("''", "'")


class Typed(object):
    """IFCLABEL('DN100') 这类"带类型包装的值"。"""
    __slots__ = ('name', 'args')

    def __init__(self, name, args):
        self.name = name
        self.args = args


class EnumV(object):
    """枚举值 .STEEL. / .T. 等。"""
    __slots__ = ('name',)

    def __init__(self, name):
        self.name = name


class _P(object):
    __slots__ = ('s', 'i', 'n')

    def __init__(self, s):
        self.s = s
        self.i = 0
        self.n = len(s)

    def ws(self):
        s, n = self.s, self.n
        i = self.i
        while i < n and s[i] in ' \t\r\n':
            i += 1
        self.i = i

    def parse(self):
        self.ws()
        if self.i >= self.n:
            return None
        s, n = self.s, self.n
        c = s[self.i]
        if c == '(':
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.i >= self.n:
                    break
                ch = s[self.i]
                if ch == ')':
                    self.i += 1
                    break
                if ch == ',':
                    self.i += 1
                    continue
                out.append(self.parse())
            return out
        if c == "'":
            j = self.i + 1
            buf = []
            while j < self.n:
                k = s.find("'", j)
                if k < 0:
                    buf.append(s[j:])
                    j = self.n
                    break
                if k + 1 < self.n and s[k + 1] == "'":
                    buf.append(s[j:k + 2])
                    j = k + 2
                    continue
                buf.append(s[j:k])
                j = k + 1
                break
            self.i = j
            return decode_ifc_str(''.join(buf))
        if c == '#':
            j = self.i + 1
            while j < self.n and s[j].isdigit():
                j += 1
            v = s[self.i:j]
            self.i = j
            return v
        if c == '.':
            j = s.find('.', self.i + 1)
            if j < 0:
                j = self.n - 1
            v = s[self.i + 1:j]
            self.i = j + 1
            return EnumV(v)
        if c in '$*':
            self.i += 1
            return None
        if c == '"':
            j = s.find('"', self.i + 1)
            if j < 0:
                j = self.n
            v = s[self.i:j + 1]
            self.i = j + 1
            return v
        # 数字 / 关键字 / 带类型包装的值
        j = self.i
        while j < self.n and s[j] not in ',()':
            j += 1
        tok = s[self.i:j].strip()
        self.i = j
        if j < self.n and s[j] == '(':
            inner = self.parse()          # 吃掉 (...)
            return Typed(tok, inner)
        return _num(tok)


def _num(t):
    if not t:
        return None
    try:
        if '.' in t or 'e' in t or 'E' in t:
            return float(t)
        return int(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return t


def parse_args(s):
    """把 'a,b,(c,d),#1' 解析成参数列表。"""
    p = _P(s)
    out = []
    while True:
        p.ws()
        if p.i >= p.n:
            break
        if p.s[p.i] == ',':
            p.i += 1
            continue
        out.append(p.parse())
    return out


# ===========================================================================
# 2. SPF 文件解析
# ===========================================================================
_RE_STMT = re.compile(r'^\s*#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\((.*)\)\s*$', re.S)


def iter_statements(fh, chunk_size=1 << 20):
    """流式切分 STEP 语句（正确处理引号内的分号与 '' 转义）。

    注意两个跨块陷阱（曾导致 >1MB 的模型被吞成一条超大语句、进而内存爆掉）：
      1) 某块里没有 ';' 但有引号时，必须 continue 继续按字符串状态扫描本块剩余内容，
         早期写成 break，会把该块从引号起到块尾的内容整段丢掉，引号配对随之错乱；
      2) 引号恰好落在块尾时无法判定它是"闭合引号"还是 "'' 转义" 的前半，
         必须把判断推迟到下一块首字符（pending 标志）。
    """
    cur = []
    in_q = False
    pending = False          # 块尾引号已并入 cur，等下一块首字符判定是否为 '' 转义
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            break
        i, n = 0, len(chunk)
        if pending:
            if chunk[0] == "'":
                cur.append("'")          # 补上 '' 的第二个引号，仍在字符串内
                i = 1
            else:
                in_q = False             # 它是闭合引号
            pending = False
        while i < n:
            if in_q:
                j = chunk.find("'", i)
                if j < 0:
                    cur.append(chunk[i:])
                    i = n
                    break
                if j + 1 >= n:
                    cur.append(chunk[i:j + 1])
                    i = n
                    pending = True
                    break
                if chunk[j + 1] == "'":
                    cur.append(chunk[i:j + 2])
                    i = j + 2
                    continue
                cur.append(chunk[i:j + 1])
                in_q = False
                i = j + 1
            else:
                j = chunk.find(';', i)
                k = chunk.find("'", i)
                if j < 0:
                    if k >= 0:
                        cur.append(chunk[i:k + 1])
                        in_q = True
                        i = k + 1
                        continue             # ← 关键：继续扫本块，不能 break
                    cur.append(chunk[i:])
                    i = n
                    break
                if k >= 0 and k < j:
                    cur.append(chunk[i:k + 1])
                    in_q = True
                    i = k + 1
                else:
                    cur.append(chunk[i:j])
                    yield ''.join(cur)
                    cur = []
                    i = j + 1
    if cur:
        yield ''.join(cur)


def _ref(x):
    if isinstance(x, str) and x.startswith('#') and x[1:].isdigit():
        return int(x[1:])
    return None


def _val(x):
    """剥掉 Typed / Enum 包装，返回 Python 原生值。"""
    if x is None:
        return None
    if isinstance(x, Typed):
        a = x.args
        if isinstance(a, list) and a:
            return _val(a[0])
        return None
    if isinstance(x, list):
        return [_val(i) for i in x]
    if isinstance(x, EnumV):
        u = x.name.upper()
        if u == 'T':
            return True
        if u == 'F':
            return False
        if u == 'U':
            return None
        return u
    return x


def _typed_name(x):
    """取出 Typed 的类型名（用于判断单位/量纲）。"""
    if isinstance(x, Typed):
        return (x.name or '').upper()
    return ''


class Model(object):
    """解析后的 IFC 模型：实体表 + 关系索引（属性/类型/材质/空间）。

    内存要点：`ents` 存 (类型, 原始参数串) —— 约等于文件大小；
    `_args` 是"已分词参数"的缓存，单条通常是 5-10 个小对象，**远比原始串重**。
    实测不加限制时，参数缓存会吃掉全部内存的一半以上（51MB 模型 → 872MB RSS）。
    这里给缓存设上限并在超限时整体清空：解析时对同一实体的访问是"就近连续"的
    （同一段 pset / 同一个放置坐标系），所以周期性清空几乎不影响命中率，
    却把内存压回可控范围。
    """

    _ARGS_CACHE_MAX = 60000

    def __init__(self):
        self.ents = {}          # eid -> (TYPE, raw_args)
        self.schema = ''
        self.header = {}
        self._args = {}
        self.by_type = {}
        # 关系索引
        self.obj2psets = {}     # element eid -> [pset eid]
        self.obj2type = {}      # element eid -> type eid
        self.obj2mat = {}       # element eid -> material eid
        self.obj2storey = {}    # element eid -> storey eid
        self.storey_elev = {}
        self.storey_name = {}
        self.unit_scale = 1.0   # 文件长度单位 → mm 的换算系数
        self.unit_name = ''

    # ---- 基础访问 ----
    def type_of(self, eid):
        e = self.ents.get(eid)
        return e[0] if e else None

    def args(self, eid):
        a = self._args.get(eid)
        if a is None:
            e = self.ents.get(eid)
            a = parse_args(e[1]) if e else []
            if len(self._args) >= self._ARGS_CACHE_MAX:
                self._args.clear()
            self._args[eid] = a
        return a

    def raw(self, eid):
        return self.ents[eid][1]

    def at(self, eid, idx):
        a = self.args(eid)
        return a[idx] if idx < len(a) else None

    def sval(self, eid, idx):
        return _val(self.at(eid, idx))

    def ref(self, eid, idx):
        return _ref(self.at(eid, idx))

    def refs(self, eid, idx):
        v = self.at(eid, idx)
        if isinstance(v, list):
            return [r for r in (_ref(x) for x in v) if r is not None]
        r = _ref(v)
        return [r] if r is not None else []

    def of_type(self, tname):
        return self.by_type.get(tname, ())

    # ---- 通用元素属性 ----
    def guid(self, eid):
        return self.sval(eid, 0)

    def name(self, eid):
        return self.sval(eid, 2)

    def objtype(self, eid):
        return self.sval(eid, 4)


def parse_ifc(path):
    """解析 IFC 文件为 Model。"""
    m = Model()
    with open(path, 'rb') as fh_raw:
        # IFC 规范为 ISO-8859-1 字节流；\X2\ 转义承载非 ASCII
        import io
        fh = io.TextIOWrapper(fh_raw, encoding='latin-1', newline='')
        in_data = False
        for stmt in iter_statements(fh):
            s = stmt.strip()
            if not s:
                continue
            if not in_data:
                if s.upper().startswith('DATA'):
                    in_data = True
                    continue
                low = s.lower()
                if low.startswith('file_schema'):
                    mm = re.search(r"'([^']+)'", s)
                    if mm:
                        m.schema = mm.group(1).strip().upper()
                elif low.startswith('file_description') or low.startswith('file_name'):
                    m.header.setdefault('raw', []).append(s[:400])
                continue
            if s.upper().startswith('ENDSEC'):
                in_data = False
                continue
            if len(s) > 4 * 1024 * 1024:
                # 正常 IFC 单条语句不可能到 4MB；出现即文件损坏，跳过以免正则回溯吃内存
                continue
            mm = _RE_STMT.match(s)
            if not mm:
                continue
            eid = int(mm.group(1))
            # 类型名 intern：实体动辄上百万个，不去重的话光是类型字符串就要几十 MB
            typ = sys.intern(mm.group(2).upper())
            m.ents[eid] = (typ, mm.group(3))
            m.by_type.setdefault(typ, []).append(eid)
    _build_index(m)
    return m


def _build_index(m):
    # 属性集关系
    for rid in list(m.of_type('IFCRELDEFINESBYPROPERTIES')):
        pset = m.ref(rid, 5)
        if pset is None:
            continue
        for o in m.refs(rid, 4):
            m.obj2psets.setdefault(o, []).append(pset)
    # 类型关系
    for rid in list(m.of_type('IFCRELDEFINESBYTYPE')):
        t = m.ref(rid, 5)
        if t is None:
            continue
        for o in m.refs(rid, 4):
            m.obj2type[o] = t
    # 材质关系（IFC4 用 IfcRelAssociatesMaterial，2X3 同名）
    for rid in list(m.of_type('IFCRELASSOCIATESMATERIAL')):
        mat = m.ref(rid, 5)
        if mat is None:
            continue
        for o in m.refs(rid, 4):
            m.obj2mat[o] = mat
    # 空间包含 → 楼层
    for rid in list(m.of_type('IFCRELCONTAINEDINSPATIALSTRUCTURE')):
        st = m.ref(rid, 5)
        if st is None:
            continue
        if m.type_of(st) in ('IFCBUILDINGSTOREY',):
            for o in m.refs(rid, 4):
                m.obj2storey[o] = st
    for sid in list(m.of_type('IFCBUILDINGSTOREY')):
        m.storey_name[sid] = m.sval(sid, 2)
        try:
            m.storey_elev[sid] = _val(m.at(sid, 9))
        except Exception:
            m.storey_elev[sid] = None
    # 单位
    _resolve_units(m)


def _resolve_units(m):
    projs = list(m.of_type('IFCPROJECT'))
    ua = None
    for p in projs:
        ua = m.ref(p, 8)          # IfcProject.UnitsInContext
        if ua:
            break
    if ua is None:
        uas = list(m.of_type('IFCUNITASSIGNMENT'))
        ua = uas[0] if uas else None
    if ua is None:
        return
    for u in m.refs(ua, 0):
        t = m.type_of(u)
        if t == 'IFCSIUNIT':
            utype = _val(m.at(u, 1))
            prefix = _val(m.at(u, 2))
            uname = _val(m.at(u, 3))
            if str(utype).upper() == 'LENGTHUNIT':
                scale = 1000.0                      # 缺省按米 → mm
                if str(uname).upper() == 'METRE':
                    scale = 1000.0
                pfx = str(prefix).upper() if prefix else ''
                mult = {'MILLI': 1.0, 'CENTI': 10.0, 'DECI': 100.0,
                        'KILO': 1000000.0, 'MICRO': 0.001, '': 1000.0}
                scale = mult.get(pfx, 1000.0)
                m.unit_scale = scale
                m.unit_name = 'mm' if scale == 1.0 else ('m' if scale == 1000.0 else str(uname))
        elif t == 'IFCCONVERSIONBASEDUNIT':
            utype = _val(m.at(u, 1))
            if str(utype).upper() == 'LENGTHUNIT':
                mwu = m.ref(u, 3)
                if mwu:
                    v = _val(m.at(mwu, 0))
                    comp = m.ref(mwu, 1)
                    if isinstance(v, (int, float)) and comp:
                        base = 1.0
                        if m.type_of(comp) == 'IFCSIUNIT':
                            pfx = _val(m.at(comp, 2))
                            base = {'MILLI': 0.001, 'CENTI': 0.01, '': 1.0}.get(
                                str(pfx).upper() if pfx else '', 1.0)
                        m.unit_scale = v * base * 1000.0
                        m.unit_name = 'converted'


# ===========================================================================
# 3. 几何（4x4 变换 + 中心线抽取）
# ===========================================================================
def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _v_len(a):
    return math.sqrt(_v_dot(a, a))


def _v_norm(a, fb=(0.0, 0.0, 1.0)):
    L = _v_len(a)
    if L < 1e-12:
        return fb
    return (a[0] / L, a[1] / L, a[2] / L)


class Mat(object):
    """坐标系：x/y/z 基向量 + 原点。"""
    __slots__ = ('x', 'y', 'z', 't')

    def __init__(self, x=(1, 0, 0), y=(0, 1, 0), z=(0, 0, 1), t=(0, 0, 0)):
        self.x, self.y, self.z, self.t = x, y, z, t

    def rot(self, v):
        return (self.x[0] * v[0] + self.y[0] * v[1] + self.z[0] * v[2],
                self.x[1] * v[0] + self.y[1] * v[1] + self.z[1] * v[2],
                self.x[2] * v[0] + self.y[2] * v[1] + self.z[2] * v[2])

    def point(self, v):
        r = self.rot(v)
        return (r[0] + self.t[0], r[1] + self.t[1], r[2] + self.t[2])


IDENT = Mat()


def mat_mul(A, B):
    """A ∘ B：先 B 后 A。"""
    return Mat(A.rot(B.x), A.rot(B.y), A.rot(B.z), A.point(B.t))


def _pt3(m, eid):
    if eid is None:
        return None
    c = _val(m.at(eid, 0))
    if isinstance(c, list) and len(c) >= 3:
        return (float(c[0]), float(c[1]), float(c[2]))
    if isinstance(c, list) and len(c) == 2:
        return (float(c[0]), float(c[1]), 0.0)
    return None


def _dir3(m, eid):
    if eid is None:
        return None
    d = _val(m.at(eid, 0))
    if isinstance(d, list) and len(d) >= 3:
        return (float(d[0]), float(d[1]), float(d[2]))
    if isinstance(d, list) and len(d) == 2:
        return (float(d[0]), float(d[1]), 0.0)
    return None


def axis_placement(m, eid):
    """IFCAXIS2PLACEMENT3D / 2D → Mat。"""
    if eid is None:
        return IDENT
    t = m.type_of(eid)
    if t == 'IFCAXIS2PLACEMENT3D':
        loc = _pt3(m, m.ref(eid, 0)) or (0.0, 0.0, 0.0)
        z = _dir3(m, m.ref(eid, 1)) or (0.0, 0.0, 1.0)
        xr = _dir3(m, m.ref(eid, 2)) or (1.0, 0.0, 0.0)
    elif t == 'IFCAXIS2PLACEMENT2D':
        loc2 = _pt3(m, m.ref(eid, 0)) or (0.0, 0.0, 0.0)
        loc = (loc2[0], loc2[1], 0.0)
        z = (0.0, 0.0, 1.0)
        xr = _dir3(m, m.ref(eid, 1)) or (1.0, 0.0, 0.0)
    else:
        return IDENT
    z = _v_norm(z)
    x = _v_sub(xr, _v_mul(z, _v_dot(xr, z)))
    if _v_len(x) < 1e-9:
        alt = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
        x = _v_sub(alt, _v_mul(z, _v_dot(alt, z)))
    x = _v_norm(x, (1.0, 0.0, 0.0))
    y = _v_cross(z, x)
    return Mat(x, y, z, loc)


def world_matrix(m, eid, depth=0):
    """IfcLocalPlacement 链 → 世界坐标 Mat。"""
    if eid is None or depth > 40:
        return IDENT
    t = m.type_of(eid)
    if t == 'IFCLOCALPLACEMENT':
        parent = world_matrix(m, m.ref(eid, 0), depth + 1)
        rel = axis_placement(m, m.ref(eid, 1))
        return mat_mul(parent, rel)
    if t in ('IFCGRIDPLACEMENT', 'IFCLINEARPLACEMENT'):
        return axis_placement(m, m.ref(eid, 1))
    return IDENT


def element_matrix(m, eid, depth=0):
    """构件的世界坐标（含 Representation 中 Placement 的兜底）。"""
    return world_matrix(m, m.ref(eid, 5), depth)


def _curve_points(m, cid, out, depth=0):
    """把曲线实体展开成点列（对象坐标系）。"""
    if cid is None or depth > 12:
        return out
    t = m.type_of(cid)
    if t == 'IFCPOLYLINE':
        for p in m.refs(cid, 0):
            pt = _pt3(m, p)
            if pt:
                out.append(pt)
    elif t == 'IFCINDEXEDPOLYCURVE':
        pl = m.ref(cid, 0)
        if pl and m.type_of(pl) == 'IFCCARTESIANPOINTLIST3D':
            raw = m.at(pl, 0)
            if isinstance(raw, list):
                for item in raw:
                    v = _val(item)
                    if isinstance(v, list) and len(v) >= 3:
                        out.append((float(v[0]), float(v[1]), float(v[2])))
    elif t == 'IFCCOMPOSITECURVE':
        for seg in m.refs(cid, 0):
            _curve_points(m, m.ref(seg, 2), out, depth + 1)
    elif t in ('IFCTRIMMEDCURVE', 'IFCBOUNDEDCURVE'):
        _curve_points(m, m.ref(cid, 0), out, depth + 1)
    elif t == 'IFCLINE':
        p = _pt3(m, m.ref(cid, 0))
        d = _dir3(m, m.ref(cid, 1))
        if p and d:
            out.append(p)
            out.append(_v_add(p, d))
    elif t in ('IFCCIRCLE', 'IFCELLIPSE'):
        pass
    return out


def solid_axis(m, item, depth=0):
    """从一个 representation item 里抽出 (p0, p1, radius)（对象坐标系）。"""
    if item is None or depth > 10:
        return None
    t = m.type_of(item)
    if t in ('IFCEXTRUDEDAREASOLID', 'IFCEXTRUDEDAREASOLIDTAPERED'):
        area = m.ref(item, 0)
        pos = axis_placement(m, m.ref(item, 1))
        d = _dir3(m, m.ref(item, 2)) or (0.0, 0.0, 1.0)
        depth_v = _val(m.at(item, 3))
        if not isinstance(depth_v, (int, float)):
            return None
        r, cen = _profile_circle(m, area)
        if r is None:
            return None
        base = pos.point(cen)
        end = _v_add(base, pos.rot(_v_mul(d, float(depth_v))))
        return (base, end, r)
    if t == 'IFCSWEPTDISKSOLID':
        pts = _curve_points(m, m.ref(item, 0), [])
        r = _val(m.at(item, 1))
        if not pts or not isinstance(r, (int, float)):
            return None
        if len(pts) == 1:
            pts = [pts[0], pts[0]]
        return (pts[0], pts[-1], float(r))
    if t == 'IFCBOOLEANRESULT' or t == 'IFCBOOLEANCLIPPINGRESULT':
        return solid_axis(m, m.ref(item, 0), depth + 1)
    if t == 'IFCHALFSPACESOLID':
        return None
    if t == 'IFCMAPPEDITEM':
        rm = m.ref(item, 0)                   # IfcRepresentationMap
        if rm:
            mtx = axis_placement(m, m.ref(rm, 0))
            rep = m.ref(rm, 1)
            sub = body_items(m, rep)
            for it in sub:
                ax = solid_axis(m, it, depth + 1)
                if ax:
                    p0 = mtx.point(ax[0])
                    p1 = mtx.point(ax[1])
                    return (p0, p1, ax[2] * _scale_of(mtx))
        return None
    if t == 'IFCSWEPTDISKSOLIDPOLYGONAL':
        pts = _curve_points(m, m.ref(item, 0), [])
        r = _val(m.at(item, 1))
        if pts and isinstance(r, (int, float)):
            return (pts[0], pts[-1], float(r))
    return None


def _scale_of(mtx):
    return _v_len(mtx.x) or 1.0


def _profile_circle(m, area):
    """截面 → (等效半径, 圆心)（截面自身坐标系）。

    圆截面给真实半径；矩形/任意截面给"等效半径"（取外包尺寸的一半），
    这样即使不是圆管（如矩形截面的管件、被误纳入的桥架）也能画出实体，
    而不是退化成原点上的一个点。等效值只用于三维显示，不参与任何合规判定。
    """
    if area is None:
        return (None, (0.0, 0.0, 0.0))
    t = m.type_of(area)
    if t in ('IFCCIRCLEPROFILEDEF', 'IFCCIRCLEHOLLOWPROFILEDEF'):
        # ProfileType(0), ProfileName(1), Position(2), Radius(3)
        r = _val(m.at(area, 3))
        cen = _pt3(m, m.ref(area, 2)) or (0.0, 0.0, 0.0)
        if isinstance(r, (int, float)):
            return (float(r), cen)
    if t == 'IFCCIRCLE':
        r = _val(m.at(area, 1))
        cen = _pt3(m, m.ref(area, 0)) or (0.0, 0.0, 0.0)
        if isinstance(r, (int, float)):
            return (float(r), cen)
    if t in ('IFCRECTANGLEPROFILEDEF', 'IFCRECTANGLEHOLLOWPROFILEDEF',
             'IFCROUNDEDRECTANGLEPROFILEDEF'):
        # XDim(3), YDim(4)
        xs = [_val(m.at(area, 3)), _val(m.at(area, 4))]
        nums = [abs(float(x)) for x in xs if isinstance(x, (int, float))]
        cen = _pt3(m, m.ref(area, 2)) or (0.0, 0.0, 0.0)
        if nums:
            return (max(nums) / 2.0, cen)
    if t in ('IFCARBITRARYCLOSEDPROFILEDEF', 'IFCARBITRARYPROFILEDEFWITHVOIDS'):
        outer = m.ref(area, 2)
        if outer and m.type_of(outer) in ('IFCCIRCLE', 'IFCCIRCLEPROFILEDEF'):
            return _profile_circle(m, outer)
        pts = _curve_points(m, outer, [])
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ex, ey = max(xs) - min(xs), max(ys) - min(ys)
            cx = (max(xs) + min(xs)) / 2.0
            cy = (max(ys) + min(ys)) / 2.0
            return (max(ex, ey) / 2.0, (cx, cy, 0.0))
    if t == 'IFCCOMPOSITEPROFILEDEF':
        for p in m.refs(area, 2):
            r, c = _profile_circle(m, p)
            if r is not None:
                return (r, c)
    if t in ('IFCELLIPSEPROFILEDEF',):
        xs = [_val(m.at(area, 3)), _val(m.at(area, 4))]
        nums = [abs(float(x)) for x in xs if isinstance(x, (int, float))]
        cen = _pt3(m, m.ref(area, 2)) or (0.0, 0.0, 0.0)
        if nums:
            return (max(nums) / 2.0, cen)
    return (None, (0.0, 0.0, 0.0))


def body_items(m, rep_eid):
    """IfcProductDefinitionShape / IfcShapeRepresentation → item 列表。"""
    out = []
    if rep_eid is None:
        return out
    t = m.type_of(rep_eid)
    if t == 'IFCPRODUCTDEFINITIONSHAPE':
        for r in m.refs(rep_eid, 2):
            out.extend(body_items(m, r))
    elif t == 'IFCSHAPEREPRESENTATION':
        ident = str(_val(m.at(rep_eid, 1)) or '').lower()
        rtype = str(_val(m.at(rep_eid, 2)) or '').lower()
        if ident in ('body', 'axis', 'footprint') or rtype in ('sweptsolid', 'brep', 'mappedrepresentation', 'advancedbrep'):
            for it in m.refs(rep_eid, 3):
                out.append(it)
    elif t in ('IFCMAPPEDITEM', 'IFCEXTRUDEDAREASOLID', 'IFCSWEPTDISKSOLID'):
        out.append(rep_eid)
    return out


def element_geometry(m, eid):
    """构件几何 → dict（mm 世界坐标）。失败返回 None，不影响合规结论。"""
    try:
        wm = element_matrix(m, eid)
        rep = m.ref(eid, 6)
        for it in body_items(m, rep):
            ax = solid_axis(m, it)
            if not ax:
                continue
            p0 = wm.point(ax[0])
            p1 = wm.point(ax[1])
            sc = m.unit_scale
            P0 = [round(p0[0] * sc, 1), round(p0[1] * sc, 1), round(p0[2] * sc, 1)]
            P1 = [round(p1[0] * sc, 1), round(p1[1] * sc, 1), round(p1[2] * sc, 1)]
            return {'k': 'cyl', 'p0': P0, 'p1': P1, 'r': round(ax[2] * sc, 2)}
        # 无实体几何 → 用放置点做标记
        t = wm.t
        return {'k': 'pt',
                'p': [round(t[0] * m.unit_scale, 1),
                      round(t[1] * m.unit_scale, 1),
                      round(t[2] * m.unit_scale, 1)]}
    except Exception:
        return None


# ===========================================================================
# 4. 属性 / 材质 / 数量 收集
# ===========================================================================
def _pset_props(m, pset_eid, out):
    t = m.type_of(pset_eid)
    if t == 'IFCPROPERTYSET':
        pname = m.sval(pset_eid, 2) or ''
        for pid in m.refs(pset_eid, 4):
            pt = m.type_of(pid)
            if pt == 'IFCPROPERTYSINGLEVALUE':
                nm = m.sval(pid, 0)
                if not nm:
                    continue
                out.append((pname, nm, _val(m.at(pid, 2)), _typed_name(m.at(pid, 2))))
            elif pt == 'IFCPROPERTYENUMERATEDVALUE':
                nm = m.sval(pid, 0)
                vv = m.at(pid, 2)
                if nm and isinstance(vv, list) and vv:
                    out.append((pname, nm, _val(vv[0]), ''))
            elif pt == 'IFCPROPERTYLISTVALUE':
                nm = m.sval(pid, 0)
                vv = _val(m.at(pid, 2))
                if nm:
                    s = ', '.join(str(x) for x in vv) if isinstance(vv, list) else str(vv)
                    out.append((pname, nm, s, ''))
    elif t == 'IFCELEMENTQUANTITY':
        pname = m.sval(pset_eid, 2) or 'Quantities'
        for qid in m.refs(pset_eid, 5):
            qt = m.type_of(qid)
            nm = m.sval(qid, 0)
            if not nm:
                continue
            for idx in (3, 4):
                v = _val(m.at(qid, idx))
                if isinstance(v, (int, float)):
                    out.append((pname, nm, v, 'QUANTITY'))
                    break
    return out


def collect_props(m, eid):
    """收集构件上全部属性：实例 Pset + 类型 Pset。返回 [(pset, name, value, typed)]。"""
    out = []
    for p in m.obj2psets.get(eid, ()):
        try:
            _pset_props(m, p, out)
        except Exception:
            pass
    tid = m.obj2type.get(eid)
    if tid:
        # 类型对象的直接 HasPropertySets
        try:
            for p in m.refs(tid, 5):
                _pset_props(m, p, out)
        except Exception:
            pass
        # 类型对象经关系挂的 Pset
        for p in m.obj2psets.get(tid, ()):
            try:
                _pset_props(m, p, out)
            except Exception:
                pass
    return out


def type_object_info(m, eid):
    tid = m.obj2type.get(eid)
    if not tid:
        return (None, None, None, None)
    return (tid, m.sval(tid, 2), m.sval(tid, 3), m.sval(tid, 7))


def material_names(m, eid, depth=0):
    """递归提取材质名。"""
    mid = m.obj2mat.get(eid)
    if mid is None or depth > 6:
        return []
    return _mat_rec(m, mid, depth)


def _mat_rec(m, mid, depth=0):
    if mid is None or depth > 6:
        return []
    t = m.type_of(mid)
    out = []
    try:
        if t == 'IFCMATERIAL':
            nm = m.sval(mid, 0)
            if nm:
                out.append(nm)
        elif t == 'IFCMATERIALLIST':
            for x in m.refs(mid, 0):
                out.extend(_mat_rec(m, x, depth + 1))
        elif t in ('IFCMATERIALLAYERSETUSAGE',):
            out.extend(_mat_rec(m, m.ref(mid, 0), depth + 1))
        elif t == 'IFCMATERIALLAYERSET':
            for l in m.refs(mid, 0):
                out.extend(_mat_rec(m, m.ref(l, 0), depth + 1))
        elif t == 'IFCMATERIALLAYER':
            out.extend(_mat_rec(m, m.ref(mid, 0), depth + 1))
        elif t == 'IFCMATERIALPROFILESETUSAGE':
            out.extend(_mat_rec(m, m.ref(mid, 0), depth + 1))
        elif t == 'IFCMATERIALPROFILESET':
            for p in m.refs(mid, 2):
                out.extend(_mat_rec(m, p, depth + 1))
        elif t == 'IFCMATERIALPROFILE':
            out.extend(_mat_rec(m, m.ref(mid, 2), depth + 1))
        elif t == 'IFCMATERIALCONSTITUENTSET':
            for c in m.refs(mid, 2):
                out.extend(_mat_rec(m, m.ref(c, 0), depth + 1))
        elif t == 'IFCMATERIALCONSTITUENT':
            # Name(0), Description(1), Material(2), Fraction(3), Category(4)
            out.extend(_mat_rec(m, m.ref(mid, 2), depth + 1))
    except Exception:
        pass
    return out


# ===========================================================================
# 5. 构件 → 管材规格 映射
# ===========================================================================
# ---------------------------------------------------------------------------
# 构件身份判定
# ---------------------------------------------------------------------------
# 坑：IFC2X3 里 IfcFlowSegment / IfcFlowFitting / IfcFlowController / IfcFlowTerminal
# 是**通用容器**，管道、电缆桥架、风管、灯具、阀门全都用它。
# 必须靠 IfcRelDefinesByType 指向的类型对象（IfcPipeSegmentType / IfcCableCarrierSegmentType…）
# 才能分清。早期版本直接按实体名归类，把"电缆桥架"和"灯具"也当成了管材，
# 于是拿管道标准的尺寸指标去评桥架，结论全是"—"。
KIND_CANDIDATE_TYPES = (
    'IFCPIPESEGMENT', 'IFCFLOWSEGMENT',
    'IFCPIPEFITTING', 'IFCFLOWFITTING',
    'IFCPIPEACCESSORY', 'IFCFLOWCONTROLLER', 'IFCVALVE', 'IFCPUMP',
    'IFCFLOWMETER', 'IFCSPRINKLER', 'IFCFLOWMOVINGDEVICE',
    'IFCFLOWSTORAGEDEVICE', 'IFCFILTER', 'IFCFLOWTERMINAL',
)

# IFC4：实体类型本身即确定身份；值为 None 表示"通用容器，要看类型对象"
KIND_DIRECT = {t: None for t in KIND_CANDIDATE_TYPES}
KIND_DIRECT.update({
    'IFCPIPESEGMENT': 'segment',
    'IFCPIPEFITTING': 'fitting',
    'IFCPIPEACCESSORY': 'accessory',
    'IFCVALVE': 'accessory',
    'IFCPUMP': 'accessory',
    'IFCFLOWMETER': 'accessory',
    'IFCSPRINKLER': 'accessory',
    'IFCFILTER': 'accessory',
    'IFCFLOWMOVINGDEVICE': 'accessory',
    'IFCFLOWSTORAGEDEVICE': 'accessory',
})

# IFC2X3：由类型对象判定身份
KIND_BY_TYPE_OBJ = {
    'IFCPIPESEGMENTTYPE': 'segment',
    'IFCPIPEFITTINGTYPE': 'fitting',
    'IFCPIPEACCESSORYTYPE': 'accessory',
    'IFCVALVETYPE': 'accessory',
    'IFCPUMPTYPE': 'accessory',
    'IFCFLOWMETERTYPE': 'accessory',
    'IFCSPRINKLERTYPE': 'accessory',
    'IFCFILTERTYPE': 'accessory',
    'IFCFIRESUPPRESSIONTERMINALTYPE': 'accessory',
    'IFCFLOWMOVINGDEVICETYPE': 'accessory',
    'IFCFLOWSTORAGEDEVICETYPE': 'accessory',
}

# 同属 Flow 家族但明确不是管材 —— 必须排除，否则会拿管道标准去评它们
KIND_NOT_PIPE = {
    'IFCCABLECARRIERSEGMENTTYPE': '电缆桥架',
    'IFCCABLECARRIERFITTINGTYPE': '电缆桥架配件',
    'IFCCABLETYPE': '电缆',
    'IFCDUCTSEGMENTTYPE': '风管',
    'IFCDUCTFITTINGTYPE': '风管管件',
    'IFCAIRTERMINALTYPE': '风口',
    'IFCAIRTERMINALBOXTYPE': '风口箱',
    'IFCLIGHTFIXTURETYPE': '灯具',
    'IFCELECTRICAPPLIANCETYPE': '电气设备',
    'IFCOUTLETTYPE': '插座',
    'IFCSWITCHINGDEVICETYPE': '开关',
    'IFCJUNCTIONBOXTYPE': '接线盒',
    'IFCELECTRICDISTRIBUTIONPOINTTYPE': '配电点',
    'IFCSANITARYTERMINALTYPE': '卫生器具',
    'IFCWASTETERMINALTYPE': '排水末端',
    'IFCELECTRICGENERATORTYPE': '发电机',
    'IFCTRANSFORMERTYPE': '变压器',
    'IFCPROTECTIVEDEVICETYPE': '保护装置',
    'IFCFLOWINSTRUMENTTYPE': '仪表',
    'IFCSPACEHEATERTYPE': '采暖设备',
    'IFCBOILERTYPE': '锅炉',
    'IFCCHILLERTYPE': '冷水机组',
    'IFCCOILTYPE': '盘管',
    'IFCFANTYPE': '风机',
    'IFCCONTROLLERTYPE': '控制器',
    'IFCSENSORTYPE': '传感器',
    'IFCUNITARYEQUIPMENTTYPE': '机组',
    'IFCENERGYCONVERSIONDEVICETYPE': '能量转换设备',
    'IFCELECTRICMOTORTYPE': '电动机',
    'IFCLAMPTYPE': '灯具',
}

_NAME_NOT_PIPE = ('桥架', '母线', '电缆', 'cabletray', 'cable tray', '风管', '风道',
                  'duct', '灯具', '插座', '开关', '配电', '线槽',
                  # 注释类构件：箭头/文字/标记等。
                  # 它们不是管材，而且坐标常离主体几十上百米，会把三维取景框撑爆
                  # （真实模型里 4 个"单独箭头/单独文字"把 scale 从 0.066 压到 0.0047，
                  #  管道在屏幕上只剩 0.23 像素 = 看不见）。
                  '箭头', '文字', '注释', '标记', '详图', '图例', '符号',
                  'annotation', 'symbol')

KIND_ZH = {'segment': '管段', 'fitting': '管件', 'accessory': '管道附件'}


def element_kind(m, eid):
    """判定构件身份 → (kind 或 None, 判定依据字符串)。

    None 表示"确认不是管材"，调用方应跳过并计入排除统计。

    判定顺序很关键：**名称检查必须在类型对象之前**。
    Revit 会把注释符号（"单独箭头-不锈钢管"、"核心 3.0 单独文字"）导出成
    IfcFlowController + IfcValveType —— 只看类型对象会把它当成阀门收进来，
    而这类构件的坐标常常离主体上百米，会把三维取景框撑爆（实测 scale 被压到 1/14，
    管道在屏幕上只剩 0.23 像素，三维看起来就是"一片空白"）。
    """
    t = m.type_of(eid)
    tid = m.obj2type.get(eid)
    tt = m.type_of(tid) if tid else None
    nm = ((m.sval(eid, 2) or '') + ' ' + (_val(m.at(eid, 4)) or '')).lower()

    # 1) 名称命中明确非管材关键词（桥架/风管/灯具/箭头/文字/注释…）
    if any(k in nm for k in _NAME_NOT_PIPE):
        return None, 'name:nonpipe'

    # 2) 类型对象 —— IFC2X3 的 Flow 通用容器必须靠它区分身份
    if tt:
        if tt in KIND_BY_TYPE_OBJ:
            return KIND_BY_TYPE_OBJ[tt], 'type:' + tt
        if tt in KIND_NOT_PIPE:
            return None, 'type:' + tt

    # 3) 实体类型本身（IFC4 专用实体直接确定身份）
    if t in KIND_DIRECT:
        direct = KIND_DIRECT[t]
        if direct:
            return direct, 'entity:' + t
        # 通用容器且无类型对象 → 再退回名称
        if any(k in nm for k in ('管', 'pipe', 'труб')):
            if t == 'IFCFLOWSEGMENT':
                return 'segment', 'name:pipe'
            if t == 'IFCFLOWFITTING':
                return 'fitting', 'name:pipe'
            return 'accessory', 'name:pipe'
        return None, 'generic:unknown'
    return None, 'not-candidate'


# 兼容旧引用：候选类型集合
KIND_BY_TYPE = {t: KIND_DIRECT[t] for t in KIND_CANDIDATE_TYPES}

# 字段映射：product 字段 → 属性名关键词（中/英/俄）
#
# 重要：Revit 管道的「直径」是**公称直径 DN**（实例参数），**不是外径**；
# 真实模型里 直径=150.0 而 外径=168.3 —— 早期把 ^直径$ 归到 od_mm 是错的，已改正。
# 「内径」单独映射，并与外径一起反推壁厚：(外径-内径)/2。
FIELD_PATTERNS = [
    ('dn_mm', [
        r'公称直径', r'公称通径', r'名义直径', r'^dn$', r'nominaldiameter',
        r'номинальныйдиаметр', r'условныйпроход', r'^diameter$', r'^直径$',
        r'^管径$', r'^尺寸$', r'^规格$', r'^标称直径$', r'尺寸.*直径']),
    ('od_mm', [
        r'^外径', r'外径', r'outerdiameter', r'outsidediameter', r'наружныйдиаметр',
        r'^od$', r'^外径\(mm\)$']),
    ('id_mm', [
        r'^内径', r'内径', r'innerdiameter', r'insidediameter', r'внутреннийдиаметр',
        r'^id$']),
    ('wall_thickness_mm', [
        # 注意：不要用宽泛的 "厚度" —— 真实模型里有「隔热层厚度」(保温层)，
        # 会被误当成管壁厚度（实测把 25mm 保温层当壁厚，产生假性不达标）。
        r'壁厚', r'管壁厚', r'wallthickness', r'толщинастенки',
        r'^thickness$', r'^толщина$']),
    # 保温/隔热层厚度单独收，避免与管壁厚度混淆（不参与合规判定，仅作证据）
    ('insulation_thickness_mm', [
        r'隔热层厚度', r'保温层厚度', r'绝热层厚度', r'insulationthickness']),
    ('material_grade_cn', [
        r'材质牌号', r'材质', r'^材料$', r'^牌号$', r'^钢号$', r'materialgrade', r'^grade$',
        r'^марка$', r'^material$', r'^сталь$', r'钢种', r'^钢材$']),
    ('working_pressure_mpa', [
        r'^公称压力', r'^工作压力', r'压力等级', r'nominalpressure', r'workingpressure',
        r'давлени', r'^pn$', r'pressure', r'^rating$']),
    ('length_m', [r'^长度$', r'^length$', r'^длина$', r'长度(?:mm|毫米)?$']),
    ('intended_use', [
        r'^用途$', r'系统类型', r'systemtype', r'назначение', r'系统名称', r'系统分类',
        r'^系统缩写$', r'systemname', r'systemclassification', r'^说明$']),
    ('medium', [r'^介质', r'medium', r'среда']),
    ('test_pressure_mpa', [r'试验压力', r'testpressure', r'испытательноедавление']),
    # 不是 product 字段，但对"判断管子是什么"极有价值：
    # Revit 的「管段」形如 "热镀锌钢管 - default"，直接给出镀锌/钢管/材质线索
    ('segment_spec', [r'^管段$', r'^管段名称$', r'^segment$', r'^管段前缀$']),
    ('connection_type', [r'^连接方式$', r'^连接类型$', r'jointtype', r'connectiontype']),
    # ГОСТ 8731/10705/20295 的「交货分组」(А/Б/В) 决定哪些指标该判、哪些该排除
    ('delivery_group', [r'^交货分组$', r'^группа\s*поставки', r'^группа$', r'deliverygroup']),
]

_NORM = re.compile(r'[\s_\-（）()\[\]【】:：/、,，.]+')


def _norm_key(s):
    return _NORM.sub('', str(s or '')).lower()


_PAT = [(f, [re.compile(p, re.I) for p in pats]) for f, pats in FIELD_PATTERNS]

# 力学/化学/试验/实测值 → product["properties"] 里的键。
# 这些正是"材质证明书"上的数据；把它们写进 IFC 的属性集后，规则引擎才能逐条判定，
# 否则永远是"未评"。真正做项目时这些值来自质保书，不是 BIM 模型本身。
PROP_PATTERNS = [
    ('rm_mpa', [r'^抗拉强度', r'^rm$', r'tensilestrength', r'временноесопротивление',
                r'пределпрочности']),
    ('re_mpa', [r'^下屈服强度', r'^屈服强度', r'^rel$', r'^reh$', r'yieldstrength',
                r'пределтекучести']),
    ('rp02_mpa', [r'rp0?\.?2', r'规定塑性延伸强度', r'условныйпределтекучести']),
    ('a_pct', [r'断后伸长率', r'^伸长率', r'elongation', r'относительноеудлинение']),
    ('chemical.C', [r'^碳含量', r'^碳$', r'^carbon$', r'углерод']),
    ('chemical.Si', [r'^硅含量', r'^硅$', r'^silicon$', r'кремни']),
    ('chemical.Mn', [r'^锰含量', r'^锰$', r'^manganese$', r'марган']),
    ('chemical.P', [r'^磷含量', r'^磷$', r'^phosphor', r'фосфор']),
    ('chemical.S', [r'^硫含量', r'^硫$', r'^sulfur', r'^sulphur', r'сера']),
    ('chemical.Cr', [r'^铬含量', r'^铬$', r'^chromium$', r'хром']),
    ('chemical.Ni', [r'^镍含量', r'^镍$', r'^nickel$', r'никел']),
    ('chemical.Cu', [r'^铜含量', r'^铜$', r'^copper$', r'мед']),
    ('hydrostatic_test_mpa', [r'水压试验', r'液压试验', r'试验压力', r'hydrostatic',
                              r'гидравлическ']),
    ('bending', [r'^弯曲试验', r'^弯曲$', r'bendingtest']),
    ('flattening', [r'^压扁试验', r'^压扁$', r'flatteningtest']),
    ('zinc_coat_g_m2', [r'镀锌层重量', r'^镀锌层', r'^锌层', r'zinc', r'цинков']),
    ('od_meas_mm', [r'^实测外径', r'外径实测']),
    ('wt_meas_mm', [r'^实测壁厚', r'壁厚实测']),
    ('len_meas_m', [r'^实测长度']),
    ('straight_meas_mm_m', [r'^弯曲度', r'^直线度', r'straightness']),
]

_PROPAT = [(f, [re.compile(p, re.I) for p in pats]) for f, pats in PROP_PATTERNS]

# 这些字段是布尔/文本型结论（如 弯曲试验 = pass），不做数值解析
_TEXT_PROPS = ('bending', 'flattening')

_UNIT_MM = {'mm': 1.0, '毫米': 1.0, 'cm': 10.0, 'm': 1000.0, '米': 1000.0,
            'in': 25.4, 'inch': 25.4, '"': 25.4, '英寸': 25.4}
_UNIT_MPA = {'mpa': 1.0, 'бар': 0.1, 'bar': 0.1, 'kpa': 0.001, 'pa': 1e-6,
             'кгс/см2': 0.0980665, 'kgf/cm2': 0.0980665, 'psi': 0.00689476}


def _extract_number(v):
    """从 '100 mm' / 'DN100' / 'Ø108×4' / 100 里取出数值（可返回多值）。"""
    if isinstance(v, bool):
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    if v is None:
        return []
    s = str(v)
    out = []
    for mm in re.finditer(r'[-+]?\d+(?:[.,]\d+)?', s):
        try:
            out.append(float(mm.group(0).replace(',', '.')))
        except ValueError:
            pass
    return out


def _to_mm(v, typed, unit_scale, prefer=None):
    """把属性值转成 mm。prefer 给出同类备选序号偏好。"""
    nums = _extract_number(v)
    if not nums:
        return None
    s = str(v).lower()
    for u, k in _UNIT_MM.items():
        if u in s:
            return round(nums[0] * k, 3)
    if typed in ('IFCLENGTHMEASURE', 'IFCPOSITIVELENGTHMEASURE',
                 'IFCNONNEGATIVELENGTHMEASURE', 'QUANTITY'):
        return round(nums[0] * unit_scale, 3)
    # 无单位线索：DN 这类"整数标注"按 mm 直读；带小数且极小则按米换算
    if len(nums) == 1:
        n = nums[0]
        if abs(n) < 3:                      # 0.06 → 60mm 之类
            return round(n * 1000.0, 3)
        return round(n, 3)
    return round(nums[0], 3)


def _to_mpa(v, typed, unit_scale):
    nums = _extract_number(v)
    if not nums:
        return None
    s = str(v).strip().lower()
    for u, k in _UNIT_MPA.items():
        if u in s:
            return round(nums[0] * k, 4)
    # PN16 / Ру16 这类"公称压力代号"：无单位且数值≥10 时按 PN/10 折算成 MPa
    if re.match(r'^\s*(?:pn|ру|py|nominalpressure)\s*\.?\s*\d', s) and nums[0] >= 10:
        return round(nums[0] / 10.0, 4)
    return round(nums[0], 4)


_GRADE_RE = re.compile(
    r'\b(Q\d{3}[A-Z]?|Q\d{3}|20#|20G|10#|16Mn|20Mn|Q345[A-Z]?|Q355[A-Z]?|'
    r'0?[0-9]{2}Cr[0-9]{1,2}Ni[0-9]{1,2}(?:Mo[0-9])?[A-Za-z]?|'
    r'1Cr18Ni9Ti|0Cr18Ni9|0Cr19Ni9|304L?|316L?|321|316Ti|'
    r'\d{2}Cr\d{2}Ni\d{2}Mo\d|10|20|35|45)\b')
_RU_GRADE_RE = re.compile(
    r'\b(Ст\d[а-яА-Я]{0,3}|0?8Х18Н10[Т]?|10Х17Н13М2Т|08Х17Н15М3Т|'
    r'09Г2С|17Г1С|20|10|35|45|12Х18Н10Т|08Х18Н10)\b', re.I)


def _pick_grade(text):
    """从材质字符串里抽出中国牌号。"""
    if not text:
        return None
    t = str(text)
    m = _GRADE_RE.search(t)
    if m:
        return m.group(1)
    return None


_STRONG_TYPE_KW = ('无缝', '焊接', '不锈钢', '镀锌', '消防', '喷淋', '涂覆', '环氧',
                   'seamless', 'welded', 'stainless', 'galvaniz', 'coated',
                   'sprinkler', 'оцинков', 'бесшов', 'нерж', 'пожар')


def _guess_type_from_text(text, material_text, grade_cn=None):
    """按文本推断产品类型（对应 evaluate.APPLICABILITY 的键）。

    返回 (类型, 是否显式)。"显式"= 模型文本里真的有类型/材质线索；
    否则只是兜底默认值——此时不能据此声称"合/不合规"，必须标注为待确认。
    """
    blob = ' '.join([str(x) for x in (text, material_text) if x]).lower()
    explicit = bool(grade_cn) or any(k in blob for k in _STRONG_TYPE_KW)
    is_ss = ('不锈钢' in blob or 'stainless' in blob or 'нерж' in blob
             or ('cr' in blob and 'ni' in blob) or 'х18н10' in blob)
    if is_ss:
        if '焊接' in blob or 'welded' in blob or 'электросвар' in blob:
            return '不锈钢焊接钢管', explicit
        return '不锈钢无缝钢管', explicit
    if '无缝' in blob or 'seamless' in blob or 'бесшов' in blob:
        return '无缝钢管', explicit
    if '镀锌' in blob or 'galvanized' in blob or 'оцинков' in blob:
        return '低压流体输送用焊接钢管(镀锌/黑管)', explicit
    if '消防' in blob or '喷淋' in blob or 'sprinkler' in blob or 'пожар' in blob or 'fire' in blob:
        return '消防涂覆钢管', explicit
    if '涂覆' in blob or '环氧' in blob or 'coated' in blob:
        return '消防涂覆钢管', explicit
    if '焊接' in blob or 'welded' in blob:
        return '直缝焊接钢管', explicit
    return '低压流体输送用焊接钢管(镀锌/黑管)', explicit


def _system_from_name(name, objtype, tname):
    """从 "管道类型:自动喷淋管:2417305" 这类名称里取出系统/用途名。

    Revit 的管道实例名通常是「族:类型:元素ID」，中间那段才是真正有用的系统名，
    可以直接当"用途/系统类型"用（如 自动喷淋管 / 加压给水管 / 消火栓管）。
    """
    for s in (name, objtype, tname):
        if not s:
            continue
        parts = [p.strip() for p in re.split(r'[:：]', str(s)) if p.strip()]
        parts = [p for p in parts if not p.isdigit()]
        if not parts:
            continue
        cand = parts[-1] if len(parts) > 1 else parts[0]
        if cand and not re.fullmatch(r'[\d\W_]+', cand):
            return cand
    return None


def element_record(m, eid):
    """把一个 IFC 构件转成统一记录（含映射后的 product 参数）。"""
    t = m.type_of(eid)
    kind, kind_basis = element_kind(m, eid)
    if kind is None:
        kind = 'other'
    # 几何先算：Revit 常常不导出直径参数，但管道实体的截面半径就是外径的一半，
    # 所以"没读到外径"其实还能从几何反推出来。
    geom = element_geometry(m, eid)
    props = collect_props(m, eid)
    mats = material_names(m, eid)
    tid, tname, tdesc, ttag = type_object_info(m, eid)

    pset_map = {}
    raw_props = {}
    typed_map = {}          # 属性名 -> IFC 量纲类型（判断"这是长度还是纯数字"的关键）
    for (ps, nm, v, typed) in props:
        raw_props[nm] = v
        if typed:
            typed_map[nm] = typed
        if ps and nm:
            pset_map.setdefault(ps, {})[nm] = v

    # 名称线索：实例名 + 类型名 + 描述 + 材质
    name = m.sval(eid, 2)
    objt = m.objtype(eid)
    tag = m.sval(eid, 7)
    mat_txt = ' / '.join([x for x in mats if x])

    field_hits = {}
    for fname, pats in _PAT:
        best = None
        for nm, v in raw_props.items():
            key = _norm_key(nm)
            for p in pats:
                if p.search(key) or p.search(str(nm)):
                    best = (nm, v, typed_map.get(nm, ''))
                    break
            if best:
                break
        if best:
            field_hits[fname] = best

    def _fh_mm(fh):
        """按属性自身的 IFC 量纲换算成 mm（长度量纲才不会把 6.0 当成 6mm）。"""
        return _to_mm(fh[1], fh[2] if len(fh) > 2 else '', m.unit_scale)

    dn = od = idv = wt = wp = None
    if 'dn_mm' in field_hits:
        dn = _fh_mm(field_hits['dn_mm'])
    if 'od_mm' in field_hits:
        od = _fh_mm(field_hits['od_mm'])
    if 'id_mm' in field_hits:
        idv = _fh_mm(field_hits['id_mm'])
    if 'wall_thickness_mm' in field_hits:
        wt = _fh_mm(field_hits['wall_thickness_mm'])
    if 'working_pressure_mpa' in field_hits:
        wp = _to_mpa(field_hits['working_pressure_mpa'][1], '', m.unit_scale)

    # 名称里的规格兜底：DN100 / Ø108×4 / 108*4 / Φ114.3
    blob = ' '.join([str(x) for x in (name, tname, tdesc, objt, tag) if x])
    if dn is None:
        mm = re.search(r'(?:DN|Ду|ДУ)\s*(\d+(?:[.,]\d+)?)', blob, re.I)
        if mm:
            dn = float(mm.group(1).replace(',', '.'))
    if od is None or wt is None:
        mm = re.search(r'[ØΦφ⌀]\s*(\d+(?:[.,]\d+)?)\s*[×xX*]\s*(\d+(?:[.,]\d+)?)', blob)
        if mm:
            od = od if od is not None else float(mm.group(1).replace(',', '.'))
            wt = wt if wt is not None else float(mm.group(2).replace(',', '.'))
        else:
            mm = re.search(r'(\d{2,3}(?:[.,]\d+)?)\s*[×xX*]\s*(\d(?:[.,]\d+)?)\b', blob)
            if mm:
                od = od if od is not None else float(mm.group(1).replace(',', '.'))
                wt = wt if wt is not None else float(mm.group(2).replace(',', '.'))

    # 几何反推外径：Revit 常常不导出"直径"参数，但管道截面半径就是外径的一半。
    od_from_geom = False
    if od is None and geom and geom.get('k') == 'cyl' and geom.get('r'):
        od = round(2.0 * float(geom['r']), 2)
        od_from_geom = True

    # 壁厚：Revit 里没有独立的"壁厚"参数，但 外径 与 内径 都有 → (外径-内径)/2。
    # 注意用 `not wt`：属性值为 0（如未设保温层）时也必须触发反推，
    # 否则会拿着 0.0 当壁厚去判定（实测踩到）。
    wt_from_id = False
    if not wt and od and idv and 0 < idv < od:
        wt = round((od - idv) / 2.0, 2)
        wt_from_id = True
    if od is None and idv and wt:
        od = round(idv + 2.0 * wt, 2)

    # 系统/用途：从「族:类型:元素ID」里取中间那段（自动喷淋管 / 加压给水管 / 消火栓管）
    sysname = _system_from_name(name, objt, tname)
    if 'intended_use' not in field_hits and sysname:
        field_hits['intended_use'] = ('(名称)', sysname, '')
        blob = blob + ' ' + sysname

    grade_txt = None
    if 'material_grade_cn' in field_hits:
        grade_txt = field_hits['material_grade_cn'][1]
    grade_cn = _pick_grade(grade_txt)
    if grade_cn is None:
        grade_cn = _pick_grade(mat_txt)
    if grade_cn is None:
        grade_cn = _pick_grade(blob)
    grade_ru = None
    if grade_cn and grade_cn in E.CN_RU_GRADE_MAP:
        grade_ru = E.CN_RU_GRADE_MAP[grade_cn]
    if grade_ru is None:
        mru = _RU_GRADE_RE.search(mat_txt or '')
        if mru:
            grade_ru = mru.group(1)

    # ---- 力学 / 化学 / 试验 / 实测值 → product["properties"] ----
    prop_vals = {}
    for fname, pats in _PROPAT:
        for nm, v in raw_props.items():
            key = _norm_key(nm)
            hit = False
            for p in pats:
                if p.search(key) or p.search(str(nm)):
                    hit = True
                    break
            if not hit:
                continue
            if fname in _TEXT_PROPS:
                prop_vals[fname] = str(_val(v)).lower() if v is not None else None
            elif fname == 'straight_meas_mm_m':
                # 弯曲度单位是 mm/m，不是长度量纲，必须取原值（不能按长度换算）
                num = _extract_number(v)
                if num:
                    prop_vals[fname] = num[0]
            elif fname.startswith('chemical.'):
                num = _extract_number(v)
                if num:
                    prop_vals.setdefault('chemical', {})[fname.split('.', 1)[1]] = num[0]
            elif fname in ('od_meas_mm', 'wt_meas_mm', 'len_meas_m'):
                m2 = _to_mm(v, typed_map.get(nm, ''), m.unit_scale)
                if m2 is not None:
                    prop_vals[fname] = m2
            else:
                num = _extract_number(v)
                if num:
                    prop_vals[fname] = num[0]
            break
    # 只保留有值的键，避免 properties 里塞一堆 None
    properties = {k: v for k, v in prop_vals.items() if v not in (None, {}, [])}

    # Revit 的「管段」形如 "热镀锌钢管 - default"，是判断"这管子到底是什么"的关键线索
    seg_spec = field_hits.get('segment_spec', (None, None))[1]
    seg_txt = str(seg_spec) if seg_spec else ''

    ptype, type_explicit = _guess_type_from_text(
        ' '.join([str(x) for x in (name, tname, tdesc, objt, tag, seg_txt) if x]),
        mat_txt, grade_cn)

    allblob = blob + ' ' + mat_txt + ' ' + seg_txt
    conn_txt = str(field_hits.get('connection_type', (None, None))[1] or '')
    galvanized = bool(re.search(r'镀锌|galvaniz|оцинков', allblob, re.I))
    threaded = bool(re.search(r'螺纹|thread|резьб|丝扣', allblob + ' ' + conn_txt, re.I))

    # 定尺长度：IFC 属性里是长度量纲（mm），product 侧统一用 m
    len_m = None
    if 'length_m' in field_hits:
        _mm = _fh_mm(field_hits['length_m'])
        if _mm is not None:
            len_m = round(_mm / 1000.0, 4)

    rec = {
        'id': eid,
        'guid': m.guid(eid),
        'ifc_type': t,
        'kind': kind,
        'kind_zh': KIND_ZH.get(kind, '其他'),
        'name': name,
        'object_type': objt,
        'tag': tag,
        'type_id': tid,
        'type_name': tname,
        'material': mat_txt or None,
        'storey': m.storey_name.get(m.obj2storey.get(eid)),
        'kind_basis': kind_basis,
        'od_from_geom': od_from_geom,
        'wt_from_id': wt_from_id,
        'geom': geom,
        'params': {
            'type': ptype,
            'dn_mm': dn,
            'od_mm': od,
            'id_mm': idv,
            'wall_thickness_mm': wt,
            'length_m': len_m,
            'working_pressure_mpa': wp,
            'material_grade_cn': grade_cn,
            'material_grade_ru': grade_ru,
            'galvanized': galvanized,
            'threaded': threaded,
            'medium': field_hits.get('medium', (None, None))[1],
            'intended_use': field_hits.get('intended_use', (None, None))[1],
            'segment_spec': seg_spec,
            'connection_type': field_hits.get('connection_type', (None, None))[1],
            'delivery_group': field_hits.get('delivery_group', (None, None))[1],
            'type_source': 'explicit' if type_explicit else 'default',
        },
        'props': raw_props,
        'psets': pset_map,
        'properties': properties,
        'evidence': {k: (str(v[1])[:120] if v[1] is not None else None)
                     for k, v in field_hits.items()},
    }
    return rec


# ===========================================================================
# 6. 规格聚合 + 合规判定
# ===========================================================================
SIG_KEYS = ('type', 'material_grade_cn', 'material_grade_ru', 'dn_mm', 'od_mm',
            'wall_thickness_mm', 'working_pressure_mpa', 'galvanized', 'threaded')


def _sig(p):
    return tuple(p.get(k) for k in SIG_KEYS)


def _spec_label(p, kind):
    bits = []
    if p.get('material_grade_cn'):
        bits.append(str(p['material_grade_cn']))
    if p.get('dn_mm'):
        bits.append('DN%s' % _fmt(p['dn_mm']))
    elif p.get('od_mm'):
        bits.append('Ø%s' % _fmt(p['od_mm']))
    if p.get('od_mm') and p.get('wall_thickness_mm'):
        bits.append('Ø%s×%s' % (_fmt(p['od_mm']), _fmt(p['wall_thickness_mm'])))
    elif p.get('wall_thickness_mm'):
        bits.append('壁厚%s' % _fmt(p['wall_thickness_mm']))
    if p.get('working_pressure_mpa'):
        bits.append('PN%s' % _fmt(p['working_pressure_mpa']))
    if p.get('galvanized'):
        bits.append('镀锌')
    return ' '.join(bits) if bits else ('未标注规格-' + KIND_ZH.get(kind, '构件'))


def _fmt(v):
    if v is None:
        return ''
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return str(v)


def _verdict_color(idx):
    """与表单版同口径：红=不达标 / 橙=边缘或数据不足 / 灰=评估不足 / 绿=通过。"""
    failed = (idx['cn']['failed'] or 0) + (idx['ru']['failed'] or 0)
    if failed:
        return 'fail'
    covs = [c for c in (idx['cn']['coverage'], idx['ru']['coverage']) if c is not None]
    if not covs or min(covs) < 0.5:
        return 'na'
    if min(covs) >= 0.75:
        return 'pass'
    return 'warn'


def analyze_ifc(path, kb=None, product_extra=None):
    """主入口：IFC → 规格表 + 中俄合规结论 + 构件几何/判定。"""
    m = parse_ifc(path)
    kb = kb or E.load_knowledge_base()

    records = []
    excluded = {}          # 被排除的非管材构件：原因 → 数量
    for tname in KIND_CANDIDATE_TYPES:
        for eid in m.by_type.get(tname, ()):    # noqa: B905
            kind, basis = element_kind(m, eid)
            if kind is None:
                reason = KIND_NOT_PIPE.get(basis.split(':', 1)[1], None) if ':' in basis else None
                key = reason or ('名称判定非管材' if basis.startswith('name:')
                                 else '通用容器无法判定')
                excluded[key] = excluded.get(key, 0) + 1
                continue
            try:
                records.append(element_record(m, eid))
            except Exception:
                pass

    # 按规格签名聚合
    groups = {}
    order = []
    for r in records:
        k = _sig(r['params'])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)

    specs = []
    for gi, k in enumerate(order, 1):
        grp = groups[k]
        head = grp[0]
        p = dict(head['params'])
        p['model'] = _spec_label(p, head['kind'])
        p['manufacturer'] = ''
        p['properties'] = dict(head.get('properties') or {})
        if product_extra:
            for kk, vv in product_extra.items():
                if vv not in (None, '', []):
                    p[kk] = vv
        # 定尺/长度
        try:
            res = E.evaluate(p, kb)
        except Exception as ex:
            res = {'error': str(ex)}
        idx = res.get('index') or {'cn': {}, 'ru': {}}
        # 类型是兜底猜的（模型里没有材质/类型线索）→ 不声称合规与否，标为"待确认"
        if p.get('type_source') == 'default':
            color = 'unknown'
        else:
            color = _verdict_color(idx) if 'index' in res else 'na'
        kinds = sorted(set(x['kind'] for x in grp))
        kind_group = ('pipe' if kinds == ['segment']
                      else ('fitting' if kinds == ['fitting']
                            else ('accessory' if kinds == ['accessory'] else 'mixed')))
        spec = {
            'key': 'S%02d' % gi,
            'signature': list(k),
            'label': p['model'],
            'count': len(grp),
            'kinds': kinds,
            'kind_group': kind_group,
            'product': p,
            'result': res,
            'color': color,
            'type_source': p.get('type_source'),
            'note': ('模型未提供材质/产品类型线索，适用标准无法确定，本条结论仅供参考'
                     if color == 'unknown' else None),
            'elements': [x['id'] for x in grp],
            'evidence': head['evidence'],
            'material': head['material'],
            'type_name': head['type_name'],
        }
        specs.append(spec)
        for x in grp:
            x['spec'] = spec['key']
            x['color'] = color

    # 几何已在 element_record 里算过（外径要靠它反推），这里只清掉体积大的中间结构
    for r in records:
        try:
            del r['psets']
        except Exception:
            pass

    # 汇总（"待确认"规格不计入合规统计，避免用猜测的类型去判人合规）
    judged = [s for s in specs if s['color'] != 'unknown']
    cov_cn = [s['result'].get('index', {}).get('cn', {}).get('coverage') for s in judged]
    cov_cn = [c for c in cov_cn if c is not None]
    cov_ru = [s['result'].get('index', {}).get('ru', {}).get('coverage') for s in judged]
    cov_ru = [c for c in cov_ru if c is not None]

    def _sum(side):
        p = sum((s['result'].get('index', {}).get(side, {}).get('passed') or 0) for s in judged)
        f = sum((s['result'].get('index', {}).get(side, {}).get('failed') or 0) for s in judged)
        n = sum((s['result'].get('index', {}).get(side, {}).get('na') or 0) for s in judged)
        covs = cov_cn if side == 'cn' else cov_ru
        return {'passed': p, 'failed': f, 'na': n,
                'coverage': round(sum(covs) / len(covs), 4) if covs else None}

    totals = {
        'elements': len(records),
        'specs': len(specs),
        'judged_specs': len(judged),
        'unknown_specs': len(specs) - len(judged),
        'by_kind': {},
        'spec_color': {},
        'excluded': excluded,
        'excluded_total': sum(excluded.values()),
        'cn': _sum('cn'),
        'ru': _sum('ru'),
    }
    for r in records:
        totals['by_kind'][r['kind']] = totals['by_kind'].get(r['kind'], 0) + 1
    for s in specs:
        totals['spec_color'][s['color']] = totals['spec_color'].get(s['color'], 0) + s['count']

    return {
        'model': {
            'file': os.path.basename(path),
            'schema': m.schema,
            'unit': m.unit_name,
            'unit_scale_mm': m.unit_scale,
            'project': (m.sval(list(m.of_type('IFCPROJECT'))[0], 2)
                        if m.of_type('IFCPROJECT') else None),
            'storeys': [m.storey_name.get(s) for s in m.of_type('IFCBUILDINGSTOREY')][:60],
        },
        'totals': totals,
        'specs': specs,
        'elements': records,
    }


# ===========================================================================
# 7. 属性清单诊断（回答"为什么没读到尺寸参数"）
# ===========================================================================
_DIM_HINT = re.compile(
    r'直径|外径|内径|壁厚|厚度|管径|口径|尺寸|规格|'
    r'diameter|thickness|radius|size|dimension|'
    r'диаметр|толщин|размер|сечени', re.I)

_MAP_FIELDS = ('dn_mm', 'od_mm', 'wall_thickness_mm', 'material_grade_cn',
               'working_pressure_mpa', 'length_m', 'intended_use', 'medium')


def _field_of_prop(name):
    """某属性名会落到哪个 product 字段（不落则 None）。"""
    key = _norm_key(name)
    for fname, pats in _PAT:
        for p in pats:
            if p.search(key) or p.search(str(name)):
                return fname
    return None


def inventory_ifc(path, kb=None):
    """列出模型里管道构件到底带了哪些属性，以及哪些字段一个都没映射上。

    这是排查"IFC 里明明有壁厚/外径，系统却没读到"的关键工具：
      - mapped        : 成功映射到 product 字段的属性名
      - dim_unmapped  : 名字看着像尺寸/规格，但没能映射上的属性名 ← 重点看这个
      - absent_fields : 一条都没映射到的字段（说明模型里根本没这个参数）
    """
    m = parse_ifc(path)
    type_counts = sorted(((len(v), t) for t, v in m.by_type.items()), reverse=True)

    # 只统计**判定为管材**的构件（IFC2X3 的 Flow 容器里混着桥架/风管/灯具）
    pipe_ids = []
    excluded = {}
    for tname in KIND_CANDIDATE_TYPES:
        for eid in m.by_type.get(tname, ()):
            kind, basis = element_kind(m, eid)
            if kind is None:
                key = basis.split(':', 1)[1] if ':' in basis else basis
                key = KIND_NOT_PIPE.get(key, '通用容器无法判定')
                excluded[key] = excluded.get(key, 0) + 1
                continue
            pipe_ids.append((tname, eid, kind))
    ptc = {}
    for tname, eid, kind in pipe_ids:
        ptc[tname] = ptc.get(tname, 0) + 1
    pipe_type_counts = sorted(((c, t) for t, c in ptc.items()), reverse=True)

    props = {}          # (pset, name) -> info
    mapped = {}         # field -> {'n': 元素数, 'from': {属性名: 次数}}
    field_elems = {}    # field -> 命中元素数
    n_elems = 0
    geom_od = 0         # 外径来自几何反推（而非属性）的构件数

    for tname, eid, kind in pipe_ids:
        n_elems += 1
        try:
            rec = element_record(m, eid)
        except Exception:
            continue
        if rec.get('od_from_geom'):
            geom_od += 1
        hits = set()
        for (ps, pn, v, typed) in collect_props(m, eid):
            if not pn:
                continue
            k = (ps or '(无属性集名)', pn)
            s = props.get(k)
            if s is None:
                s = props[k] = {'n': 0, 'samples': [], 'types': set(),
                                'typed': set(), 'field': None}
            s['n'] += 1
            s['types'].add(tname)
            if typed:
                s['typed'].add(typed)
            if len(s['samples']) < 3 and v is not None and str(v) != '':
                s['samples'].append(str(v)[:60])
            f = s['field'] or _field_of_prop(pn)
            if f:
                s['field'] = f
                mapped.setdefault(f, {'n': 0, 'from': {}})
                mapped[f]['from'][pn] = mapped[f]['from'].get(pn, 0) + 1
                hits.add(f)
        for f in hits:
            field_elems[f] = field_elems.get(f, 0) + 1

    # 名字像尺寸却没映射上的 —— 最可能导致"尺寸没读到"
    dim_unmapped = []
    for (ps, pn), s in props.items():
        if s['field'] is None and _DIM_HINT.search(pn):
            dim_unmapped.append({'pset': ps, 'name': pn, 'count': s['n'],
                                 'samples': s['samples'],
                                 'ifc_types': sorted(s['types'])[:4]})
    dim_unmapped.sort(key=lambda x: -x['count'])

    absent = [f for f in _MAP_FIELDS if field_elems.get(f, 0) == 0]

    # 有些字段"属性里没有"但"可以算出来"——要单独说明，否则用户会以为真的缺
    derivable = []
    if field_elems.get('od_mm') and field_elems.get('id_mm'):
        derivable.append({'field': 'wall_thickness_mm',
                          'how': '壁厚 = (外径 − 内径) / 2',
                          'note': 'Revit 没有独立的壁厚参数，但外径/内径都有，可直接算'})
    if field_elems.get('dn_mm') and field_elems.get('od_mm'):
        derivable.append({'field': 'dn_mm/od_mm 交叉校验',
                          'how': '按 DN→外径 对照表核对',
                          'note': '用于发现"名义 DN 与实际外径不匹配"的管子'})

    all_props = []
    for (ps, pn), s in sorted(props.items(), key=lambda kv: -kv[1]['n'])[:400]:
        all_props.append({'pset': ps, 'name': pn, 'count': s['n'],
                          'mapped_to': s['field'], 'samples': s['samples'],
                          'ifc_types': sorted(s['types'])[:4],
                          'value_types': sorted(s['typed'])[:4]})

    return {
        'model': {
            'file': os.path.basename(path),
            'schema': m.schema,
            'length_unit': m.unit_name,
            'unit_scale_mm': m.unit_scale,
        },
        'entity_types': [{'type': t, 'count': c} for c, t in type_counts[:40]],
        'pipe_types': [{'type': t, 'count': c} for c, t in pipe_type_counts],
        'pipe_elements': n_elems,
        'excluded_nonpipe': excluded,
        'od_from_geometry': geom_od,
        'mapped_fields': {f: {'elements': field_elems.get(f, 0),
                              'from_properties': sorted(v['from'])[:12]}
                          for f, v in mapped.items()},
        'absent_fields': absent,
        'derivable_fields': derivable,
        'dim_like_unmapped': dim_unmapped[:60],
        'all_properties': all_props,
    }


def render_inventory(inv):
    """把诊断结果打成可读文本。"""
    L = []
    L.append('=' * 74)
    L.append('IFC 属性清单诊断：%s   schema=%s   长度单位=%s (×%s → mm)'
             % (inv['model']['file'], inv['model']['schema'],
                inv['model']['length_unit'], inv['model']['unit_scale_mm']))
    L.append('=' * 74)
    L.append('管道类构件共 %d 个' % inv['pipe_elements'])
    L.append('管道类 IFC 实体：%s'
             % ', '.join('%s×%d' % (x['type'], x['count']) for x in inv['pipe_types']))
    if inv.get('excluded_nonpipe'):
        L.append('已排除的非管材构件（同属 Flow 家族，但按类型对象判定不是管材）：')
        for k, v in sorted(inv['excluded_nonpipe'].items(), key=lambda kv: -kv[1]):
            L.append('    × %-16s %d 个' % (k, v))
    L.append('')
    L.append('【已成功读到的字段】')
    if inv['mapped_fields']:
        for f, v in inv['mapped_fields'].items():
            L.append('  ✔ %-22s 命中 %d 个构件   来自属性: %s'
                     % (f, v['elements'], ', '.join(v['from_properties'])))
    else:
        L.append('  （一个都没有）')
    L.append('')
    L.append('【完全没读到的字段】')
    if inv['absent_fields']:
        for f in inv['absent_fields']:
            L.append('  ✘ %s' % f)
    else:
        L.append('  （全部字段都至少命中一次）')
    L.append('')
    if inv.get('od_from_geometry'):
        L.append('【几何反推补充】外径：%d 个构件的截面半径直接反推出外径'
                 '（模型没有导出"直径"参数，但几何里有）' % inv['od_from_geometry'])
        L.append('')
    if inv.get('derivable_fields'):
        L.append('【属性里没有、但可以算出来的字段】')
        for x in inv['derivable_fields']:
            L.append('  ≈ %-22s %s' % (x['field'], x['how']))
            L.append('      %s' % x['note'])
        L.append('')
    L.append('【名字像尺寸/规格、但没能映射上的属性】← 重点排查')
    if inv['dim_like_unmapped']:
        for x in inv['dim_like_unmapped']:
            L.append('  ! [%s] %s  出现 %d 次  样例=%s'
                     % (x['pset'], x['name'], x['count'], x['samples'][:2]))
    else:
        L.append('  （无）')
    L.append('')
    L.append('【出现最多的属性（前25）】')
    for x in inv['all_properties'][:25]:
        L.append('  %-28s %-30s ×%-6d → %s'
                 % (x['pset'][:28], x['name'][:30], x['count'],
                    x['mapped_to'] or '—'))
    return '\n'.join(L)


# ===========================================================================
# 8. CLI
# ===========================================================================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    if sys.argv[1] == 'inspect':
        if len(sys.argv) < 3:
            print('用法: python engine/ifc_pipe.py inspect <file.ifc> [out.json]')
            return 1
        inv = inventory_ifc(sys.argv[2])
        print(render_inventory(inv))
        if len(sys.argv) > 3:
            with open(sys.argv[3], 'w', encoding='utf-8') as f:
                json.dump(inv, f, ensure_ascii=False, indent=1)
            print('\nJSON 已写入 %s' % sys.argv[3])
        return 0
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '_analysis.json'
    data = analyze_ifc(src)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    t = data['totals']
    print('模型: %s (%s)' % (data['model']['file'], data['model']['schema']))
    print('构件: %d 个 / 规格: %d 种' % (t['elements'], t['specs']))
    print('分类: %s' % t['by_kind'])
    print('判定色分布: %s' % t['spec_color'])
    print('中国侧: 通过 %s / 不通过 %s / 未评 %s' % (t['cn']['passed'], t['cn']['failed'], t['cn']['na']))
    print('俄国侧: 通过 %s / 不通过 %s / 未评 %s' % (t['ru']['passed'], t['ru']['failed'], t['ru']['na']))
    print('输出: %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
