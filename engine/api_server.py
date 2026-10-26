#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中俄消防金属管材合规评价 —— HTTP API 服务（FastAPI）
=====================================================
把 engine/evaluate.py 的 evaluate(product, kb) 包成 REST API，
供微信小程序/任意客户端调用。对 evaluate.py 零改动。

用法（本地跑通）:
    pip install fastapi uvicorn
    python engine/api_server.py                 # 默认 http://127.0.0.1:8000
    # 然后另开终端:
    curl -X POST http://127.0.0.1:8000/api/evaluate \
         -H "Content-Type: application/json" \
         -d @examples/sample_input.json

部署（微信云托管 CloudBase Run，免 ICP 备案）:
    1. 用本目录 Dockerfile 构建镜像推送至腾讯云镜像仓库
    2. 云托管服务监听端口 8000，健康检查路径 /health
    3. 小程序端 baseURL 填云托管分配的域名（https://xxx.tcloudbaseapp.com）

依赖: fastapi, uvicorn（evaluate.py 本身仅标准库）
"""
import json
import sys
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# 无论以 `python engine/api_server.py` 还是以 `engine.api_server:app` 启动，
# 都保证能 import 到同目录的 evaluate / llm_report / pipeline。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evaluate as E
import llm_report as LR
import ifc_pipe as IP
from model_page import MODEL_HTML

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

app = FastAPI(
    title="CN-RU Fire Pipe Compliance API",
    description="中俄消防金属管材认证指标自动对比评价引擎",
    version="0.1.0",
)

# 小程序请求默认带 referer/origin，放开跨域避免联调困扰（生产可收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 知识库缓存：服务启动时加载一次（data/cn_standards_final.json + ru_standards.json）
_KB = None


def get_kb():
    global _KB
    if _KB is None:
        try:
            _KB = E.load_knowledge_base()
        except Exception as e:  # 知识库缺失时给可读错误
            raise HTTPException(status_code=500, detail=f"知识库加载失败: {e}")
    return _KB


class EvaluateRequest(BaseModel):
    """与 examples/sample_input.json 的 product 对象同构。"""
    product: dict
    lang: Optional[str] = "zh"


@app.get("/health")
def health():
    return {"status": "ok", "kb_loaded": _KB is not None}


# 网页输入表单页：打开 http://服务器/ 即可填参数、点按钮看结果
INDEX_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中俄消防管材合规 Agent</title>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;background:#eef1f6;color:#1f2430;max-width:960px;margin:0 auto;padding:24px 16px}
 h1{font-size:20px;margin:4px 0}
 .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin:14px 0;box-shadow:0 1px 3px rgba(16,24,40,.06)}
 label{font-size:13px;color:#555} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
 input,select{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box;font-size:14px}
 select:disabled,input:disabled{background:#f0f1f4;color:#a9aeb8;cursor:not-allowed}
 select option:disabled{color:#a9aeb8}
 button{padding:10px 18px;border:0;border-radius:8px;background:#1f6feb;color:#fff;font-size:15px;cursor:pointer;margin:6px 6px 0 0}
 button.gray{background:#6b7280}
 .t{font-weight:700;margin:0 0 10px;font-size:15px} .muted{font-size:12px;color:#888}
 .hint{font-size:11.5px;color:#98a1ad;margin-top:4px;line-height:1.5}
 .brand{display:flex;align-items:center;gap:8px;font-size:18px}
 .langbtn{background:#1f6feb;color:#fff;border:0;border-radius:14px;padding:4px 12px;font-size:12.5px;cursor:pointer;font-weight:600}
 .langbtn:hover{background:#3b82f6}
 #out{display:none;margin-top:16px}
 /* ---- 报告渲染区（浅色文档风格）---- */
 .report{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:22px 26px;box-shadow:0 1px 3px rgba(16,24,40,.06);color:#1f2430;line-height:1.7;font-size:14.5px}
 .report h1{font-size:22px;color:#0b1f4d;border-bottom:2px solid #1f6feb;padding-bottom:10px;margin:0 0 6px}
 .report h2{font-size:17px;color:#1f6feb;margin:20px 0 8px;padding-left:10px;border-left:4px solid #1f6feb}
 .report h3{font-size:15px;color:#0b1f4d;margin:14px 0 6px}
 .report p{margin:6px 0}
 .report ul{margin:6px 0 6px;padding-left:22px}
 .report li{margin:3px 0}
 .report blockquote{background:#f3f6fc;border-left:4px solid #1f6feb;margin:12px 0;padding:8px 14px;border-radius:0 8px 8px 0;color:#46536b;font-size:13.5px}
 .report table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
 .report th{background:#eef3fb;border:1px solid #dbe3f0;padding:8px 10px;text-align:left;color:#0b1f4d}
 .report td{border:1px solid #e5e7eb;padding:7px 10px;vertical-align:top}
 .report tr:nth-child(even) td{background:#fafbfe}
 .report strong{color:#0b1f4d}
 .report .warn{color:#e67e22;font-weight:700}
 .report .ok{color:#16772f;font-weight:700}
 .report .bad{color:#c0262f;font-weight:700}
 .report .mask{color:#8a5a2b;font-style:italic;font-weight:700}
 .report .ru{font-family:Georgia,'Times New Roman',serif}
 .report code{background:#f0f2f7;padding:1px 5px;border-radius:4px;font-size:13px}
 /* ---- NA 折叠块（未评估项）---- */
 .report details.na{border:1px solid #f0c36f;border-left:4px solid #e67e22;background:#fffaf3;border-radius:8px;margin:8px 0;padding:6px 12px}
 .report details.na summary{cursor:pointer;font-size:13.5px;color:#b45309;font-weight:600;list-style:none;display:flex;align-items:center;gap:8px}
 .report details.na summary::before{content:'▸';transition:transform .15s;color:#e67e22}
 .report details.na[open] summary::before{transform:rotate(90deg)}
 .report details.na summary:hover{color:#c2410c}
 .report details.na .na-body{margin-top:6px;font-size:13px;color:#8a5a2b;line-height:1.6}
 .report details.na .na-body b{color:#b45309}
 .report details.warn{border:1px solid #f0c36f}
 pre.json{background:#0b1220;color:#d1e3ff;padding:14px 18px;border-radius:10px;white-space:pre-wrap;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;overflow-x:auto}
</style></head><body>
<div class="brand">🇨🇳🇷🇺 <h1 id="pageTitle">中俄消防金属管材合规 Agent</h1>
 <span style="margin-left:auto;font-size:12.5px;display:flex;align-items:center;gap:10px">
  <button class="langbtn" id="langBtn" onclick="toggleLang()" title="切换界面语言 / Сменить язык">Рус</button>
  <a href="/chat">对话版</a> · <a href="/model">模型分析</a> · <a href="/terms">⚖ 免责声明</a></span></div>
<div class="muted" data-i18n="填管材参数 → 生成集体评价报告（当前 mock；填 LLM_API_KEY 后为真实模型报告）。">填管材参数 → 生成集体评价报告（当前 mock；填 LLM_API_KEY 后为真实模型报告）。</div>
<div class="warn" style="background:#fffaf3;border:1px solid #f0c36f;border-left:4px solid #e67e22;border-radius:8px;padding:8px 12px;margin:8px 0;font-size:12.5px;color:#8a5a2b">
 ⚖ 本工具输出为<b>技术合规初筛参考</b>，<b>不构成法律意见或认证承诺</b>；最终结论须由认证机构 / 认可实验室（ГОСТ ISO/IEC 17025-2019）确认。
 <br><b>本站仅作学习交流使用</b>，不作为商业宣传或服务承诺。
 <a href="/terms">查看完整免责声明与法律风险提示 →</a>
</div>
<div class="card"><div class="t" data-i18n="① 基本">① 基本</div><div class="grid">
 <div><label data-i18n="型号">型号</label><select id="model" onchange="applyModel(this.value)">
   <option value="DN100 热浸镀锌焊接钢管">DN100 热浸镀锌焊接钢管</option>
   <option value="DN50 热浸镀锌焊接钢管">DN50 热浸镀锌焊接钢管</option>
   <option value="DN100 环氧涂覆钢管(消防)">DN100 环氧涂覆钢管(消防)</option>
   <option value="DN150 无缝钢管(20#)">DN150 无缝钢管(20#)</option>
   <option value="DN100 不锈钢无缝管(304)">DN100 不锈钢无缝管(304)</option>
   <option value="DN200 直缝焊接钢管">DN200 直缝焊接钢管</option>
 </select></div>
 <div><label data-i18n="产品类型">产品类型</label><select id="type" onchange="updateCoat()">
   <option value="消防涂覆钢管">消防涂覆钢管</option>
   <option value="低压流体输送用焊接钢管(镀锌/黑管)">低压流体输送用焊接钢管(镀锌/黑管)</option>
   <option value="无缝钢管">无缝钢管</option><option value="直缝焊接钢管">直缝焊接钢管</option><option value="冷拔无缝钢管">冷拔无缝钢管</option>
   <option value="不锈钢无缝钢管">不锈钢无缝钢管</option><option value="不锈钢焊接钢管">不锈钢焊接钢管</option>
   <option value="干线管道焊接钢管（工业·非消防）">干线管道焊接钢管（工业·非消防）</option>
 </select></div>
 <div><label data-i18n="中牌号">中牌号</label><select id="gene_cn" onchange="linkGene()">
   <option value="Q235B">Q235B</option><option value="Q195">Q195</option><option value="Q215A">Q215A</option><option value="Q215B">Q215B</option><option value="Q235A">Q235A</option><option value="Q275A">Q275A</option><option value="Q275B">Q275B</option><option value="Q355B">Q355B</option>
   <option value="10">10</option><option value="20">20</option><option value="Q345D">Q345D</option><option value="Q390D">Q390D</option><option value="Q420D">Q420D</option><option value="Q460D">Q460D</option>
   <option value="HRB400">HRB400</option><option value="HRB400E">HRB400E</option><option value="HRB500">HRB500</option><option value="HRB500E">HRB500E</option>
   <option value="06Cr19Ni10">06Cr19Ni10</option><option value="022Cr19Ni10">022Cr19Ni10</option><option value="06Cr17Ni12Mo2">06Cr17Ni12Mo2</option><option value="022Cr17Ni12Mo2">022Cr17Ni12Mo2</option><option value="06Cr18Ni11Ti">06Cr18Ni11Ti</option>
 </select></div>
 <div><label data-i18n="俄牌号">俄牌号</label><select id="gene_ru">
   <option value="Ст1сп">Ст1сп</option><option value="Ст1пс">Ст1пс</option><option value="Ст1кп">Ст1кп</option>
   <option value="Ст2сп">Ст2сп</option><option value="Ст2пс">Ст2пс</option><option value="Ст2кп">Ст2кп</option>
   <option value="Ст3сп">Ст3сп</option><option value="Ст3пс">Ст3пс</option><option value="Ст3кп">Ст3кп</option>
   <option value="Ст4сп">Ст4сп</option><option value="Ст4пс">Ст4пс</option><option value="Ст4кп">Ст4кп</option>
   <option value="20">20</option><option value="10">10</option><option value="Ст20">Ст20</option><option value="Ст10">Ст10</option>
   <option value="09Г2С">09Г2С</option><option value="17Г1С">17Г1С</option><option value="14ХГС">14ХГС</option>
   <option value="12Х18Н10Т">12Х18Н10Т</option><option value="08Х18Н10">08Х18Н10</option><option value="08Х18Н10Т">08Х18Н10Т</option><option value="08Х17Н15М3Т">08Х17Н15М3Т</option><option value="17Х18Н9">17Х18Н9</option><option value="10Х17Н13М2Т">10Х17Н13М2Т</option>
   <option value="А400С">А400С</option><option value="А500С">А500С</option>
 </select></div>
</div></div>
<div class="card"><div class="t" data-i18n="② 尺寸与工况">② 尺寸与工况</div><div class="grid">
 <div><label data-i18n="DN / mm">DN / mm</label><input id="dn" type="number" value="100"></div>
 <div><label data-i18n="外径 mm">外径 mm</label><input id="od" type="number" value="114"></div>
 <div><label data-i18n="壁厚 mm">壁厚 mm</label><input id="wt" type="number" value="4.0"></div>
 <div><label data-i18n="长度 m">长度 m</label><input id="len" type="number" value="6"></div>
 <div><label data-i18n="压力 MPa">压力 MPa</label><input id="p" type="number" value="1.6"></div>
 <div><label data-i18n="介质组">介质组</label><select id="grp"><option value="2">组2(水/蒸汽/空气)</option><option value="1">组1(危险介质)</option></select></div>
 <div><label data-i18n="相态">相态</label><select id="phase"><option value="liquid">液体</option><option value="gas">气体</option></select></div>
 <div><label data-i18n="用途">用途</label><select id="use">
   <option value="自动喷水灭火系统配水管道">自动喷水灭火系统配水管道</option>
   <option value="室内消火栓系统给水管道">室内消火栓系统给水管道</option>
   <option value="消防给水/消防管道">消防给水/消防管道</option>
   <option value="生活与消防合用给水管道">生活与消防合用给水管道</option>
   <option value="一般给水/排水管道（非消防）">一般给水/排水管道（非消防）</option>
   <option value="燃气输送管道（非消防）">燃气输送管道（非消防）</option>
   <option value="工业用水/蒸汽管道（非消防）">工业用水/蒸汽管道（非消防）</option>
 </select></div>
</div></div>
<div class="card"><div class="t" data-i18n="③ 检测数据（可留空；留空标「未提供/需判定」）">③ 检测数据（可留空；留空标「未提供/需判定」）</div><div class="grid">
 <div><label data-i18n="Rm（抗拉强度）MPa">Rm（抗拉强度）MPa</label><input id="rm" type="number" value="400"></div>
 <div><label data-i18n="ReL（下屈服强度）MPa">ReL（下屈服强度）MPa</label><input id="re" type="number" value="245"></div>
 <div><label data-i18n="A（断后伸长率）%">A（断后伸长率）%</label><input id="a" type="number" value="26"></div>
 <div><label data-i18n="液压试验（水压）MPa">液压试验（水压）MPa</label><input id="hydro" type="number" value="2.5"></div>
 <div><label data-i18n="镀锌层重量 g/m²">镀锌层重量 g/m²</label><input id="zinc" type="number" value="350" oninput="zincToThk()"></div>
  <div><label data-i18n="镀锌层厚度 μm">镀锌层厚度 μm</label><input id="zinc_um" type="number" step="0.1" placeholder="自动" oninput="zincToWt()" disabled></div>
 <div><label data-i18n="涂层厚度（涂覆钢管，DN分档）mm">涂层厚度（涂覆钢管，DN分档）mm</label><input id="coat_thk" type="number" value="0.40" step="0.01"></div>
 <div><label data-i18n="涂层附着力 级">涂层附着力 级</label><input id="coat_adh" type="number" value="2" min="1" max="3"></div>
 <div><label data-i18n="C（碳含量上限）%">C（碳含量上限）%</label><input id="chemC" type="number" value="0.18" step="0.001"></div>
 <div><label data-i18n="Si（硅含量上限）%">Si（硅含量上限）%</label><input id="chemSi" type="number" value="0.30" step="0.001"></div>
 <div><label data-i18n="Mn（锰含量上限）%">Mn（锰含量上限）%</label><input id="chemMn" type="number" value="0.60" step="0.001"></div>
 <div><label data-i18n="P（磷含量上限）%">P（磷含量上限）%</label><input id="chemP" type="number" value="0.030" step="0.001"></div>
 <div><label data-i18n="S（硫含量上限）%">S（硫含量上限）%</label><input id="chemS" type="number" value="0.030" step="0.001"></div>
  <div><label data-i18n="Cr（铬含量）%">Cr（铬含量）%</label><input id="chemCr" type="number" step="0.01" placeholder="不锈钢需填"></div>
  <div><label data-i18n="Ni（镍含量）%">Ni（镍含量）%</label><input id="chemNi" type="number" step="0.01" placeholder="不锈钢需填"></div>
  <div><label data-i18n="Mo（钼含量）%">Mo（钼含量）%</label><input id="chemMo" type="number" step="0.01" placeholder="不锈钢需填"></div>
  <div><label data-i18n="Rp02（0.2%规定塑性延伸强度）MPa">Rp02（0.2%规定塑性延伸强度）MPa</label><input id="rp02" type="number" step="1" placeholder="无缝/不锈钢需填"></div>
  <div><label data-i18n="外径实测 mm（尺寸公差）">外径实测 mm（尺寸公差）</label><input id="od_meas" type="number" step="0.01" placeholder="实测外径"></div>
  <div><label data-i18n="壁厚实测 mm（尺寸公差）">壁厚实测 mm（尺寸公差）</label><input id="wt_meas" type="number" step="0.01" placeholder="实测壁厚"></div>
  <div><label data-i18n="长度实测 m">长度实测 m</label><input id="len_meas" type="number" step="0.01" placeholder="实测长度"></div>
  <div><label data-i18n="弯曲度实测 mm/m">弯曲度实测 mm/m</label><input id="str_meas" type="number" step="0.01" placeholder="实测弯曲度"></div>
  <div><label data-i18n="交货分组（俄标 ГОСТ）">交货分组（俄标 ГОСТ）</label><select id="group"><option value="">未注明</option><option value="А">А（只定力学）</option><option value="Б">Б（只定化学）</option><option value="В">В（力学+化学）</option></select><div class="hint">按 ГОСТ 8731-87 §1.1 / 10705-80：交付时保证哪些性能。А=只保证力学；Б=只保证化学成分；В=力学+化学都保证。</div></div>
 <div><label data-i18n="压扁试验">压扁试验</label><select id="flatten"><option value="">未做</option><option value="pass">合格</option><option value="fail">不合格</option></select></div>
 <div><label data-i18n="弯曲试验">弯曲试验</label><select id="bend"><option value="">未做</option><option value="pass">合格</option><option value="fail">不合格</option></select></div>
 <div><label data-i18n="无损检测（NDT）">无损检测（NDT）</label><select id="ndt"><option value="">未做</option><option value="pass">合格</option><option value="fail">不合格</option></select></div>
 <div><label data-i18n="连接方式">连接方式</label><select id="conn">
    <option value="沟槽连接">沟槽连接</option>
    <option value="螺纹连接">螺纹连接</option>
    <option value="法兰连接">法兰连接</option>
    <option value="焊接">焊接</option>
    <option value="卡压连接">卡压连接</option>
    <option value="平口/未注明">平口/未注明</option>
  </select></div></div></div>
<div class="card">
 <button onclick="go('report')" data-i18n="生成集体评价报告">生成集体评价报告</button>
 <button class="gray" onclick="go('evaluate')" data-i18n="只看结构化判定">只看结构化判定</button>
</div>
<div id="out"></div>
<script>

// ================= 表单页界面语言（中 / 俄） =================
var LANG=(function(){try{return localStorage.getItem('dsh_lang')||'zh'}catch(e){return 'zh'}})();
var I18N_RU={
"填管材参数 → 生成集体评价报告（当前 mock；填 LLM_API_KEY 后为真实模型报告）。":"Заполните параметры трубы → отчёт о соответствии (сейчас режим mock).",
"① 基本":"① Основное","② 尺寸与工况":"② Размеры и условия",
"③ 检测数据（可留空；留空标「未提供/需判定」）":"③ Данные испытаний (можно не заполнять)",
"型号":"Модель","产品类型":"Тип продукции","中牌号":"Марка (КНР)","俄牌号":"Марка (РФ)",
"DN / mm":"DN / мм","外径 mm":"Наружный диаметр, мм","壁厚 mm":"Толщина стенки, мм","长度 m":"Длина, м",
"压力 MPa":"Давление, МПа","介质组":"Группа среды","相态":"Фаза","用途":"Назначение",
"Rm（抗拉强度）MPa":"Rm (предел прочности), МПа","ReL（下屈服强度）MPa":"ReL (предел текучести), МПа",
"A（断后伸长率）%":"A (относительное удлинение), %","液压试验（水压）MPa":"Гидроиспытание, МПа",
"镀锌层重量 g/m²":"Масса цинк. покрытия, г/м²","镀锌层厚度 μm":"Толщина цинк. покрытия, мкм",
"涂层厚度（涂覆钢管，DN分档）mm":"Толщина покрытия (по DN), мм","涂层附着力 级":"Адгезия покрытия, балл",
"C（碳含量上限）%":"C (углерод), %","Si（硅含量上限）%":"Si (кремний), %","Mn（锰含量上限）%":"Mn (марганец), %",
"P（磷含量上限）%":"P (фосфор), %","S（硫含量上限）%":"S (сера), %","Cr（铬含量）%":"Cr (хром), %",
"Ni（镍含量）%":"Ni (никель), %","Mo（钼含量）%":"Mo (молибден), %",
"Rp02（0.2%规定塑性延伸强度）MPa":"Rp0.2 (условный предел текучести), МПа",
"外径实测 mm（尺寸公差）":"Факт. наружный диаметр, мм","壁厚实测 mm（尺寸公差）":"Факт. толщина стенки, мм",
"长度实测 m":"Факт. длина, м","弯曲度实测 mm/m":"Факт. кривизна, мм/м",
"交货分组（俄标 ГОСТ）":"Группа поставки (ГОСТ)","压扁试验":"Испытание на сплющивание",
"弯曲试验":"Испытание на изгиб","无损检测（NDT）":"Неразрушающий контроль (НК)","连接方式":"Тип соединения",
"生成集体评价报告":"Сформировать отчёт","只看结构化判定":"Только результат (JSON)"};
var OPT_RU={
model:{"DN100 热浸镀锌焊接钢管":"DN100 горячеоцинкованная сварная","DN50 热浸镀锌焊接钢管":"DN50 горячеоцинкованная сварная","DN100 环氧涂覆钢管(消防)":"DN100 с эпоксидным покрытием (пожарная)","DN150 无缝钢管(20#)":"DN150 бесшовная (сталь 20)","DN100 不锈钢无缝管(304)":"DN100 нержавеющая бесшовная (304)","DN200 直缝焊接钢管":"DN200 прямошовная сварная"},
type:{"消防涂覆钢管":"Труба с покрытием (пожарная)","低压流体输送用焊接钢管(镀锌/黑管)":"Сварная труба (оцинк./чёрная)","无缝钢管":"Бесшовная труба","直缝焊接钢管":"Прямошовная сварная труба","冷拔无缝钢管":"Холоднодеформированная бесшовная","不锈钢无缝钢管":"Нержавеющая бесшовная","不锈钢焊接钢管":"Нержавеющая сварная","干线管道焊接钢管（工业·非消防）":"Магистральная сварная (пром.)"},
grp:{"2":"Группа 2 (вода/пар/воздух)","1":"Группа 1 (опасная среда)"},
phase:{"liquid":"Жидкость","gas":"Газ"},
use:{"自动喷水灭火系统配水管道":"Распределительный трубопровод АУПТ","室内消火栓系统给水管道":"Внутренний противопожарный водопровод","消防给水/消防管道":"Противопожарное водоснабжение","生活与消防合用给水管道":"Хоз.-противопожарный водопровод","一般给水/排水管道（非消防）":"Общий водопровод (не пожарный)","燃气输送管道（非消防）":"Газопровод (не пожарный)","工业用水/蒸汽管道（非消防）":"Пром. вода/пар (не пожарный)"},
group:{"":"не указано","А":"А (только механика)","Б":"Б (только химия)","В":"В (механика+химия)"},
flatten:{"":"не проводилось","pass":"годен","fail":"не годен"},
bend:{"":"не проводилось","pass":"годен","fail":"не годен"},
ndt:{"":"не проводилось","pass":"годен","fail":"не годен"},
conn:{"沟槽连接":"Жёлобчатое","螺纹连接":"Резьбовое","法兰连接":"Фланцевое","焊接":"Сварное","卡压连接":"Пресс-фитинговое","平口/未注明":"Гладкий конец"}};
var TITLE_RU="ИИ-агент соответствия пожарных труб КНР/РФ";
function i18nStatic(){
 var t=document.getElementById('pageTitle');
 if(t){ if(!t.dataset.zh)t.dataset.zh=t.textContent;
  t.textContent=(LANG==='ru')?TITLE_RU:t.dataset.zh; }
 document.querySelectorAll('[data-i18n]').forEach(function(el){
  var k=el.getAttribute('data-i18n');
  if(!el.dataset.zh) el.dataset.zh=el.textContent;
  var ru=I18N_RU[k];
  el.textContent=(LANG==='ru'&&ru)?ru:el.dataset.zh;
 });
 document.getElementById('langBtn').textContent=(LANG==='zh')?'Рус':'中文';
}
function i18nOptions(){
 document.querySelectorAll('select').forEach(function(sel){
  var dic=OPT_RU[sel.id]; if(!dic) return;
  Array.prototype.forEach.call(sel.options,function(o){
   if(!o.dataset.zh) o.dataset.zh=o.textContent;
   var ru=dic[o.value];
   o.textContent=(LANG==='ru'&&ru)?ru:o.dataset.zh;
  });
 });
}
function applyLang(){ i18nStatic(); i18nOptions(); }
function toggleLang(){ LANG=(LANG==='zh')?'ru':'zh'; try{localStorage.setItem('dsh_lang',LANG)}catch(e){} applyLang(); }
// ===========================================================

function num(x){var v=parseFloat(x);return isNaN(v)?null:v}
function set(id,v){document.getElementById(id).value=v}
// 中→俄 基本对应（与引擎/报告基本对应一致；Q 系选сп，不锈钢用权威映射）
var CN_TO_RU={'Q195':'Ст1сп','Q215A':'Ст2сп','Q215B':'Ст2сп','Q215':'Ст2сп','Q235A':'Ст3сп','Q235B':'Ст3сп','Q235':'Ст3сп',
 'Q275A':'Ст4сп','Q275B':'Ст4сп','Q275':'Ст4сп','Q355B':'09Г2С','Q345D':'09Г2С','Q390D':'09Г2С','Q420D':'17Г1С','Q460D':'14ХГС',
 '10':'10','20':'20',
 '06Cr19Ni10':'08Х18Н10','022Cr19Ni10':'08Х18Н10','06Cr17Ni12Mo2':'10Х17Н13М2Т',
 '022Cr17Ni12Mo2':'08Х17Н15М3Т','06Cr18Ni11Ti':'08Х18Н10Т'};
// 选中中牌号 → 自动带出对应俄牌号（用户仍可手动改俄牌号）
function linkGene(){var cn=val('gene_cn');if(cn&&CN_TO_RU[cn]!=null){set('gene_ru',CN_TO_RU[cn]);}}
var MODELS={
 'DN50 热浸镀锌焊接钢管':{type:'低压流体输送用焊接钢管(镀锌/黑管)',cn:'Q235B',ru:'Ст3сп',dn:50,od:60,wt:3.8,p:1.6,len:6,conn:'螺纹连接'},
 'DN100 热浸镀锌焊接钢管':{type:'低压流体输送用焊接钢管(镀锌/黑管)',cn:'Q235B',ru:'Ст3сп',dn:100,od:114,wt:4.0,p:1.6,len:6,conn:'螺纹连接'},
 'DN100 环氧涂覆钢管(消防)':{type:'消防涂覆钢管',cn:'Q235B',ru:'Ст3сп',dn:100,od:114,wt:4.0,p:1.6,len:6,fire:1,conn:'沟槽连接'},
 'DN150 无缝钢管(20#)':{type:'无缝钢管',cn:'20',ru:'20',dn:150,od:159,wt:6.0,p:2.5,len:6,conn:'沟槽连接'},
 'DN100 不锈钢无缝管(304)':{type:'不锈钢无缝钢管',cn:'06Cr19Ni10',ru:'08Х18Н10',dn:100,od:114,wt:4.0,p:1.6,len:6,conn:'沟槽连接'},
 'DN200 直缝焊接钢管':{type:'直缝焊接钢管',cn:'Q235B',ru:'Ст3сп',dn:200,od:219,wt:6.0,p:1.6,len:6,conn:'焊接'}
};
function applyModel(m){var d=MODELS[m];if(!d)return;
 set('type',d.type);set('gene_cn',d.cn);set('gene_ru',d.ru);
 set('dn',d.dn);set('od',d.od);set('wt',d.wt);set('p',d.p);set('len',d.len);
 if(d.fire) set('use','自动喷水灭火系统配水管道');
 if(d.conn) set('conn',d.conn);
 updateCoat();
}
// 只在"涂覆"类管材才有涂层厚度/附着力要求；其它类型禁用涂层输入（不可选中）
function updateCoat(){
 var t=val('type')||'';
 var isCoat=(t.indexOf('涂覆')>=0);
 var thk=document.getElementById('coat_thk'),adh=document.getElementById('coat_adh');
 [thk,adh].forEach(function(el){
  if(!isCoat){el.value='';el.disabled=true;}
  else{el.disabled=false;}
 });
 updateThread();
 updateSpecial();
}
// 连接方式联动：仅"基管标准有螺纹要求"的管型（GB/T 3091 镀锌/黑管、消防涂覆(基管3091)）
// 才可选"螺纹连接"；无缝/不锈钢/直缝/冷拔/干线等以焊接/法兰/平口连接为主 → 禁用"螺纹连接"并归为"沟槽连接"。
function updateThread(){
 var t=val('type')||'';
 var needsThread=(t.indexOf('镀锌')>=0 || t.indexOf('黑管')>=0 || t.indexOf('涂覆')>=0);
 var conn=document.getElementById('conn');
 if(conn){
  var opts=conn.options;
  for(var i=0;i<opts.length;i++){
   if(opts[i].value==='螺纹连接'){opts[i].disabled=!needsThread;}
  }
  if(!needsThread && conn.value==='螺纹连接'){conn.value='沟槽连接';}
 }
}
// 合金元素 / Rp02 联动：
//  - 不锈钢（含"不锈钢"）→ Cr/Ni/Mo 必填（须填）、Rp02 可用
//  - 无缝（含"无缝"但不含"不锈钢"）→ Rp02 可用
//  - 其它管材 → Cr/Ni/Mo/Rp02 全部禁用（不可选择）
function updateSpecial(){
 var t=val('type')||'';
 var isSS=(t.indexOf('不锈钢')>=0);
 var isSeam=(t.indexOf('无缝')>=0);
 var ids=['chemCr','chemNi','chemMo','rp02'];
 ids.forEach(function(id){
  var el=document.getElementById(id); if(!el)return;
  var use=isSS || (id==='rp02' && isSeam);
  if(!use){el.value='';el.disabled=true;el.required=false;}
  else{el.disabled=false;el.required=isSS;} // Cr/Ni/Mo/Rp0.2 不锈钢必填；碳钢无缝 Rp0.2 选填
 });
}
// zinc density: 1 um ~= 7.14 g/m2
var ZINC_DENSITY=7.14;
function zincToThk(){var w=num(val('zinc'));var u=document.getElementById('zinc_um');
 if(w==null){u.value='';return;} u.value=(Math.round(w/ZINC_DENSITY*100)/100);}
function zincToWt(){var t=num(val('zinc_um'));var w=document.getElementById('zinc');
 if(t==null){w.value='';return;} w.value=(Math.round(t*ZINC_DENSITY*100)/100);}
function readP(){
 return {product:{
  model:val('model'),type:val('type'),
  material_grade_cn:val('gene_cn'),material_grade_ru:val('gene_ru'),
  dn_mm:num(val('dn')),od_mm:num(val('od')),wall_thickness_mm:num(val('wt')),length_m:num(val('len')),
  working_pressure_mpa:num(val('p')),medium_group:parseInt(val('grp')),medium_phase:val('phase'),
  intended_use:val('use'),
  connection:val('conn'),
  threaded: val('conn')==='螺纹连接',
  properties:{rm_mpa:num(val('rm')),re_mpa:num(val('re')),a_pct:num(val('a')),
    hydrostatic_test_mpa:num(val('hydro')),zinc_coat_g_m2:num(val('zinc')),zinc_coat_thickness_um:num(val('zinc_um')),
    coating_thickness_mm:isCoat()?num(val('coat_thk')):null,coating_adhesion_grade:isCoat()?num(val('coat_adh')):null,
    flattening:val('flatten'),bending:val('bend'),ndt:val('ndt'),
    delivery_group:val('group'),
    rp02_mpa:num(val('rp02')),
    od_meas_mm:num(val('od_meas')),wt_meas_mm:num(val('wt_meas')),len_meas_m:num(val('len_meas')),straight_meas_mm_m:num(val('str_meas')),
    chemical:{C:num(val('chemC')),Si:num(val('chemSi')),Mn:num(val('chemMn')),P:num(val('chemP')),S:num(val('chemS')),
              Cr:num(val('chemCr')),Ni:num(val('chemNi')),Mo:num(val('chemMo'))}}}}}
function val(id){return document.getElementById(id).value}
function isCoat(){return (val('type')||'').indexOf('涂覆')>=0}
// ---- 轻量 Markdown → HTML（供集体评价报告渲染）----
function mdEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
// 内联：**加粗**、⚠ 橙黄、西里尔文用 Times New Roman
function mdInline(s){
  s=mdEsc(s);
  // 颜色标记：[[ok]]绿  [[bad]]红  [[mask]]灰褐  [[warn]]橙黄 把"合格/不合格/豁免/牌号不对应"等单独着色
  s=s.replace(/\[\[ok\]\]\s*(.*?)\s*\[\[\/ok\]\]/g,'<span class="ok">$1</span>');
  s=s.replace(/\[\[bad\]\]\s*(.*?)\s*\[\[\/bad\]\]/g,'<span class="bad">$1</span>');
  s=s.replace(/\[\[mask\]\]\s*(.*?)\s*\[\[\/mask\]\]/g,'<span class="mask">$1</span>');
  s=s.replace(/\[\[warn\]\]\s*(.*?)\s*\[\[\/warn\]\]/g,'<span class="warn">$1</span>');
  s=s.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  s=s.replace(/⚠️?/g,'<span class="warn">⚠</span>');
  s=s.replace(/([\u0400-\u04FF]+)/g,'<span class="ru">$1</span>');
  return s;
}
// 表格块：以 | 开头的连续行
function mdTable(lines,i){
  var rows=[],j=i;
  while(j<lines.length && /^\s*\|/.test(lines[j])) { rows.push(lines[j]); j++; }
  if(rows.length<2) return {html:[],next:j,ok:false};
  var h="<table>",headerDone=false,anyBody=false;
  for(var k=0;k<rows.length;k++){
    var raw=rows[k].trim().replace(/^\|/,'').replace(/\|$/,'');
    var cells=raw.split('|').map(function(c){return c.trim()});
    // 分隔行 |---|---|（全为 -/冒号）→ 跳过
    if(cells.length && cells.every(function(c){return /^:?-{2,}:?$/.test(c)})){continue;}
    var tag=headerDone?'td':'th';
    h+="<tr>"+cells.map(function(c){return '<'+tag+'>'+mdInline(c)+'</'+tag+'>';}).join('')+"</tr>";
    if(headerDone){} else headerDone=true;
  }
  h+="</table>";
  if(!headerDone) return {html:[],next:j,ok:false};
  return {html:[h],next:j,ok:true};
}
// 主渲染
function mdToHtml(md){
  var lines=String(md||'').split(/\r?\n/);
  var out=[],i=0;
  while(i<lines.length){
    var line=lines[i]; var t=line.trim();
    if(t===''){i++;continue;}
    if(/^\s*\|/.test(t)){var tb=mdTable(lines,i); if(tb.ok){out.push(tb.html.join('')); i=tb.next; continue;}}
    var m;
    if(m=t.match(/^#\s+(.*)/)){out.push('<h1>'+mdInline(m[1])+'</h1>'); i++; continue;}
    if(m=t.match(/^##\s+(.*)/)){out.push('<h2>'+mdInline(m[1])+'</h2>'); i++; continue;}
    if(m=t.match(/^###\s+(.*)/)){out.push('<h3>'+mdInline(m[1])+'</h3>'); i++; continue;}
    if(t.slice(0,2)==='> '){out.push('<blockquote>'+mdInline(t.slice(2))+'</blockquote>'); i++; continue;}
    // NA/提示 折叠块：:::na  标题  /  内容行...  :::
    if(/^:::\s*na/i.test(t)){
      var dn=true,title=lines[i].replace(/^:::\s*na\s*/i,'');
      var body=[]; i++;
      while(i<lines.length && !/^:::/.test(lines[i].trim())){
        var b=lines[i].trim();
        if(b) body.push(mdInline(b));
        i++;
      }
      if(i<lines.length && /^:::$/.test(lines[i].trim())) i++; // 关闭标记
      var cls='na';
      out.push('<details class="'+cls+'"><summary>'+mdInline(title||'未评估项')+'</summary><div class="na-body">'+body.join('<br>')+'</div></details>');
      continue;
    }
    if(/^[-*]\s+/.test(t)){
      var lis=[];
      while(i<lines.length && /^[-*]\s+/.test(lines[i].trim())){
        lis.push('<li>'+mdInline(lines[i].trim().replace(/^[-*]\s+/,''))+'</li>'); i++;
      }
      out.push('<ul>'+lis.join('')+'</ul>'); continue;
    }
    out.push('<p>'+mdInline(t)+'</p>'); i++;
  }
  return out.join('\n');
}
async function go(end){
 var out=document.getElementById('out');
 // 校验必填项（如不锈钢的 Cr/Ni/Mo、Rp0.2）
 var missing=[];
 ["chemCr","chemNi","chemMo","rp02"].forEach(function(id){
  var el=document.getElementById(id);
  if(el && el.required && !el.value){missing.push(el.previousElementSibling?el.previousElementSibling.textContent:id);}
 });
 if(missing.length){out.className='report';out.style.display='block';
  out.innerHTML='<p style="color:#d33;font-weight:700">⚠ 请填写必填项：'+missing.join('、')+'</p>';return;}
 if(end==='report'){ out.className='report'; out.style.display='block'; out.innerHTML='<p>生成中…</p>'; }
 else { out.className='json'; out.style.display='block'; out.textContent='请求中…'; }
 try{
  var r=await fetch('/api/'+end,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign(readP(),{lang:LANG}))});
  var d=await r.json();
  if(end==='report'){
    out.innerHTML=mdToHtml(d.report||'');
  } else {
    out.textContent = JSON.stringify(d,null,2);
  }
 }catch(e){ if(end==='report'){out.innerHTML='<p>失败: '+mdEsc(e.message)+'</p>';} else {out.textContent='失败: '+e;} }
}
// 页面加载即按默认"型号"联动填好 产品类型/牌号/尺寸，避免默认类型与型号不一致
applyLang();
applyModel(document.getElementById('model').value);
applyLang();
updateCoat();
zincToThk();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    """主入口 = 对话版；表单版移到 /form（旧链接 /app 仍保留）。"""
    return HTMLResponse(CHAT_HTML)


@app.get("/form", response_class=HTMLResponse)
def form_page():
    return HTMLResponse(INDEX_HTML)


@app.get("/app", response_class=HTMLResponse)
def index_app():
    return HTMLResponse(INDEX_HTML)


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

def api_evaluate(req: EvaluateRequest):
    """核心入口：输入产品参数 → 返回完整判定结果（JSON 序列化安全）。"""
    product = req.product
    try:
        result = E.evaluate(product, kb=get_kb())
        if (req.lang or "zh") == "ru":
            result = _localize_ru(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"评价失败: {e}")
    return _sanitize(result)


@app.post("/api/report")
def api_report(req: EvaluateRequest):
    """LLM 集体评价报告：规则引擎判定 → 报告（默认 mock；配置 LLM_API_KEY 后走 openai）。"""
    product = req.product
    try:
        result = E.evaluate(product, kb=get_kb())
        cfg = LR.get_config()
        gen = LR.generate_report(product, result, cfg, req.lang or "zh")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"报告生成失败: {e}")
    return {"provider": gen["provider"], "report": gen["report"]}


@app.get("/api/meta")
def api_meta():
    """给前端用的元信息：产品类型下拉、知识库版本等。"""
    kb = get_kb()
    cn_ids = [s["id"] for s in kb["cn"].get("standards", [])]
    ru_ids = [s["id"] for s in kb["ru"].get("standards", [])]
    return {
        "product_types": list(E.APPLICABILITY.keys()),
        "cn_standards": cn_ids,
        "ru_standards": ru_ids,
    }


# ---------------------------------------------------------------------------
# 模型分析：BIM(IFC) → 管材语义提取 → 中俄合规判定 + 三维标注
# ---------------------------------------------------------------------------
# 上限不是随便定的，是实测出来的：解析峰值内存约为文件大小的 12~15 倍
# （20MB→250MB，50MB→610MB）。服务器 1.8GB 内存、无 swap，故取 64MB。
MAX_IFC_BYTES = 64 * 1024 * 1024
MAX_PEAK_FACTOR = 15        # 解析峰值内存 ≈ 文件大小 × 该系数（实测 12~15）


@app.get("/model", response_class=HTMLResponse)
def model_page():
    return HTMLResponse(MODEL_HTML)


@app.get("/api/model/limits")
def api_model_limits():
    """给前端展示：单文件上限、服务器内存水位、解析内存估算系数。"""
    import shutil
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.split()[0]) // 1024      # KB → MB
    except Exception:
        pass
    disk_free = None
    try:
        disk_free = shutil.disk_usage("/").free // 1048576
    except Exception:
        pass
    return {
        "max_bytes": MAX_IFC_BYTES,
        "max_mb": MAX_IFC_BYTES // 1048576,
        "peak_factor": MAX_PEAK_FACTOR,
        "peak_mb_at_max": (MAX_IFC_BYTES // 1048576) * MAX_PEAK_FACTOR,
        "mem_total_mb": mem.get("MemTotal"),
        "mem_available_mb": mem.get("MemAvailable"),
        "mem_swap_mb": mem.get("SwapTotal", 0),
        "disk_free_mb": disk_free,
        "workers": 2,
    }


async def _receive_ifc(request: Request):
    """把上传流按块落盘并做头部校验，返回 (临时文件路径, 字节数)。

    不读 request.body()：那是把整个文件先堆进内存再多写一份到磁盘；
    这里流式落盘，省掉一份与文件等大的常驻内存。
    """
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".ifc", prefix="cnru_")
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_IFC_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {MAX_IFC_BYTES // 1048576}MB 上限（当前服务配置）")
                f.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传内容为空")
        with open(tmp, "rb") as f:
            head = f.read(8192).lstrip().upper()
        if not head.startswith(b"ISO-10303-21") and b"HEADER" not in head:
            raise HTTPException(
                status_code=400,
                detail="不是有效的 IFC(STEP) 文件：缺少 ISO-10303-21 头")
        return tmp, total
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@app.post("/api/model/analyze")
async def api_model_analyze(request: Request, filename: str = "model.ifc"):
    """收 IFC 原始字节，返回完整分析结果。"""
    tmp = None
    total = 0
    try:
        tmp, total = await _receive_ifc(request)
        data = IP.analyze_ifc(tmp, get_kb())
    except HTTPException:
        raise
    except MemoryError:
        raise HTTPException(
            status_code=413,
            detail="模型太大，服务器内存不足；请只导出管道相关构件后重试")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"IFC 解析失败: {e}")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    data["model"]["file"] = filename
    data["model"]["size_bytes"] = total
    # 构件级属性是响应体积的大头：构件很多时只保留规格级证据，避免响应过大
    if len(data.get("elements", [])) > 800:
        for el in data["elements"]:
            el.pop("props", None)
    else:
        for el in data.get("elements", []):
            pr = el.get("props") or {}
            el["props"] = {k: (v if isinstance(v, (int, float, bool)) or v is None
                               else str(v)[:160]) for k, v in list(pr.items())[:40]}
    return _sanitize(data)


@app.post("/api/model/inspect")
async def api_model_inspect(request: Request, filename: str = "model.ifc"):
    """属性清单诊断：模型里管道构件到底有哪些属性、哪些字段没映射上。

    用于回答"IFC 里明明有壁厚/外径，为什么系统没读到"。
    """
    tmp = None
    try:
        tmp, total = await _receive_ifc(request)
        inv = IP.inventory_ifc(tmp, get_kb())
    except HTTPException:
        raise
    except MemoryError:
        raise HTTPException(status_code=413, detail="模型太大，服务器内存不足")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"IFC 诊断失败: {e}")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    inv["model"]["file"] = filename
    inv["model"]["size_bytes"] = total
    inv["report_text"] = IP.render_inventory(inv)
    return _sanitize(inv)


def _sanitize(obj):
    """递归把非 JSON 类型（如 tuple/None 嵌套）转成可序列化结构。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return str(obj)


# ---------------------------------------------------------------------------
# 免责声明 / 法律风险提示页
# ---------------------------------------------------------------------------
TERMS_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>免责声明与法律风险提示</title>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;background:#eef1f6;color:#1f2430;margin:0;padding:20px}
 .wrap{max-width:860px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:24px 28px;line-height:1.75;font-size:14.5px}
 h1{font-size:20px;color:#0b1f4d;border-bottom:2px solid #1f6feb;padding-bottom:10px;margin:0 0 8px}
 h2{font-size:15.5px;color:#1f6feb;margin:20px 0 6px}
 ol,ul{padding-left:22px;margin:6px 0}
 li{margin:5px 0}
 .warn{background:#fffaf3;border:1px solid #f0c36f;border-left:4px solid #e67e22;border-radius:8px;padding:10px 14px;margin:12px 0;color:#8a5a2b}
 .ru{font-family:'Times New Roman',serif}
 .muted{color:#6b7280;font-size:12.5px}
 a{color:#1f6feb}
</style></head><body><div class="wrap">
<h1>免责声明 · 法律风险提示 / Отказ от ответственности</h1>
<p class="muted">最后更新：随代码部署同步 · Документ обновляется вместе с релизом</p>

<div class="warn">
<strong>核心提示：</strong>本平台输出为<b>技术合规初筛参考</b>，<b>不构成法律意见，也不构成认证结论或认证承诺</b>。
最终认证结论<b>必须由认证机构 / 认可实验室</b>（ГОСТ ISO/IEC 17025-2019）确认。
<br><span class="ru">Результаты носят <b>предварительный технический характер</b>; окончательное заключение — за органом по сертификации / аккредитованной лабораторией.</span>
</div>

<h2>一、输出性质</h2>
<ul>
<li>本平台基于<b>公开标准资料</b>整理的知识库，通过规则引擎 + 检索生成评价；<b>可能存在滞后、遗漏或理解偏差</b>。</li>
<li>知识库中部分数值仍标注 <code>[待核]</code>，须以标准<b>官方正式文本</b>为准。</li>
<li>任何商业决策请以<b>官方标准原文 + 认证机构意见</b>为准。</li>
</ul>

<h2>二、法律风险提示（运营方须注意）</h2>
<ol>
<li><b>标准文本版权</b>：GB / ГОСТ 标准文本<b>受版权保护</b>（中国标准出版社、俄罗斯标准机构等）。本平台仅引用<b>条款编号与要点</b>，不得用于替代标准原文；如需大量引用原文或表格，应另行取得授权。</li>
<li><b>生成式 AI 合规</b>：若以生成式 AI 技术向境内公众提供服务，按现行规定可能涉及<b>生成式人工智能服务备案 / 算法备案</b>；且 <b>2025-09-01 起</b> AI 生成合成内容<b>须添加标识</b>。</li>
<li><b>广告法</b>：商业宣传<b>不得使用绝对化用语</b>（如"最权威""保证通过"），<b>不得作出保证认证通过的承诺</b>。</li>
<li><b>经营主体与备案</b>：开展<b>经营性互联网信息服务</b>（咨询、收费等）须具备相应<b>主体资质</b>并依法纳税；服务器位于境外<b>不免除主体的法律责任</b>；在大陆服务器对外提供网站服务须完成 ICP 备案。</li>
<li><b>个人信息保护</b>：收集、处理用户信息须遵守《个人信息保护法》，<b>跨境传输须另行评估</b>；对话内容与产品参数可能涉及商业秘密，应采取保密与安全措施。</li>
<li><b>商标与域名</b>：域名注册<b>不等于</b>取得商标保护；使用前应完成商标检索，避免与他人在先权利冲突。</li>
</ol>

<h2>三、责任限制</h2>
<ul>
<li>使用者依据本平台输出作出的任何决策，<b>风险自担</b>；平台不承担由此产生的间接、附带或后果性损失。</li>
<li>对外提供服务时，建议在合同中明确<b>服务范围、免责声明与责任上限</b>。</li>
</ul>

<h2>四、Русский / 俄文摘要</h2>
<p class="ru">
Платформа предоставляет <b>предварительную техническую оценку соответствия</b> и <b>не является юридическим заключением или сертификатом</b>.
Итоговое заключение даёт орган по сертификации / аккредитованная лаборатория (ГОСТ ISO/IEC 17025-2019).
Тексты стандартов (GB, ГОСТ) <b>охраняются авторским правом</b> — приводятся только номера пунктов и ключевые требования.
При публичном распространении с использованием генеративного ИИ могут требоваться <b>регистрация сервиса ИИ</b> и <b>маркировка ИИ-контента</b>.
Пользователь принимает решения <b>на свой риск</b>.
</p>

<p class="muted">返回：<a href="/chat">对话版</a> · <a href="/model">模型分析</a> · <a href="/form">表单版</a></p>
</div></body></html>"""


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    return HTMLResponse(TERMS_HTML)


# ---------------------------------------------------------------------------
# RAG 对话页 / 接口
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = None   # [{"role":"user"|"bot","content":str}] 多轮上下文
    lang: Optional[str] = "zh"       # 界面/回答语言：zh / ru


@app.get("/chat", response_class=HTMLResponse)
def chat_page():
    return HTMLResponse(CHAT_HTML)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """RAG 对话：查询理解/路由 → BM25+重排检索 → 结构化回答(结论·依据·来源·追问)。"""
    import rag_chat as RC
    try:
        return RC.answer(req.message, get_kb(), req.history, (req.lang or "zh"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"对话失败: {e}")


CHAT_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中俄消防管材合规 · 智能问答</title>
<style>
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;background:#eef1f6;color:#1f2430;margin:0;height:100vh;display:flex;flex-direction:column}
 header{background:#0b1f4d;color:#fff;padding:12px 16px;font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px}
 header a{color:#9dc1ff;font-size:12px;font-weight:400;text-decoration:none}
 .hright{margin-left:auto;display:flex;align-items:center;gap:10px}
 .langbtn{background:#1f6feb;color:#fff;border:0;border-radius:14px;padding:4px 12px;font-size:12.5px;cursor:pointer;font-weight:600}
 .langbtn:hover{background:#3b82f6}
 #msgs{flex:1;overflow-y:auto;padding:16px;max-width:960px;width:100%;margin:0 auto}
 .row{display:flex;margin:10px 0;gap:8px}
 .row.me{justify-content:flex-end}
 .bub{max-width:78%;padding:10px 13px;border-radius:12px;line-height:1.65;font-size:14.5px;box-shadow:0 1px 2px rgba(16,24,40,.06)}
 .bot .bub{background:#fff;border:1px solid #e5e7eb}
 .me .bub{background:#1f6feb;color:#fff}
 .bub h3{margin:2px 0 6px;font-size:14px}
 .bub ul{margin:4px 0;padding-left:20px} .bub li{margin:2px 0}
 .bub code{background:#f2f4f8;padding:1px 5px;border-radius:4px;font-size:13px}
 .ok{color:#16772f;font-weight:700}.bad{color:#c0262f;font-weight:700}.mask{color:#8a5a2b;font-style:italic;font-weight:700}.warn{color:#e67e22;font-weight:700}
 .ru{font-family:'Times New Roman',serif}
 .refs{margin-top:8px;font-size:12.5px;color:#5b6472;border-top:1px dashed #dfe3ea;padding-top:6px}
 .refs b{color:#1f6feb}
 .chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 16px 8px;max-width:960px;margin:0 auto;width:100%}
 .chip{background:#fff;border:1px solid #d8dee8;border-radius:14px;padding:5px 11px;font-size:12.5px;color:#33415c;cursor:pointer}
 .chip:hover{background:#f4f7ff;border-color:#9dc1ff}
 .inputbar{display:flex;gap:8px;padding:12px 16px;background:#fff;border-top:1px solid #e5e7eb;max-width:960px;margin:0 auto;width:100%}
 .inputbar input{flex:1;padding:11px 13px;border:1px solid #ccc;border-radius:10px;font-size:14.5px}
 .inputbar button{padding:11px 20px;border:0;border-radius:10px;background:#1f6feb;color:#fff;font-size:14.5px;cursor:pointer}
 /* 输入框下方的三个方框快捷方式 */
 .quickbar{display:flex;gap:10px;padding:12px 16px 16px;max-width:960px;margin:0 auto;width:100%;flex-wrap:wrap}
 .qbox{flex:1 1 0;min-width:180px;display:flex;flex-direction:column;gap:3px;align-items:flex-start;
  background:#fff;border:1px solid #d7e3f7;border-radius:11px;padding:10px 14px;text-decoration:none;
  cursor:pointer;font-family:inherit;font-size:inherit;text-align:left;transition:.15s;
  box-shadow:0 1px 2px rgba(16,24,40,.05)}
 .qbox:hover{border-color:#1f6feb;background:#f7faff;transform:translateY(-1px)}
 .qbox:active{transform:translateY(0)}
 .qbox .qi{font-size:16px;line-height:1.1}
 .qbox .qt{font-size:13.5px;font-weight:700;color:#0b1f4d}
 .qbox .qd{font-size:11.5px;color:#8b97a8;line-height:1.45}
 .qbox.on{border-color:#1f6feb;background:#eef3fb}
 .qbox.on .qt{color:#1f6feb}
 .rep{background:#fbfcff;border:1px solid #dbe4f5;border-radius:10px;padding:10px 12px;margin-top:8px;max-height:340px;overflow:auto;font-size:13.5px}
 .bub blockquote{margin:4px 0;padding:4px 10px;border-left:3px solid #dbe4f5;background:#f7f9fd;color:#4a5568;font-size:13.5px;border-radius:0 6px 6px 0}
 .bub details.na{border:1px solid #f0c36f;background:#fffaf0;border-radius:8px;padding:6px 10px;margin:6px 0;font-size:13.5px}
 .bub details.na summary{cursor:pointer;color:#8a5a2b;font-weight:700}
 .bub details.na .na-body{margin-top:6px;color:#5b6472}
</style></head><body>
<header>
 <span id="hdTitle">🔥 中俄消防管材合规 · 智能问答</span>
 <span class="hright">
  <button class="langbtn" id="langBtn" onclick="toggleLang()" title="切换界面语言 / Сменить язык">Рус</button>
  <a id="termsLink" href="/terms" title="免责声明与法律风险提示">⚖ 免责声明</a>
  <a id="modelLink" href="/model" title="上传 BIM/IFC 模型做管材合规分析">🧊 模型分析</a>
  <a id="formLink" href="/form">← 表单版</a>
 </span>
</header>
<div id="msgs"></div>
<div class="chips" id="chips"></div>
<div id="notice" style="text-align:center;font-size:11.5px;color:#b45309;padding:2px 12px 6px">本站仅作学习交流使用，不作为商业宣传或服务承诺</div>
<div class="inputbar">
 <input id="q" placeholder="" onkeydown="if(event.key==='Enter')send()">
 <button id="sendBtn" onclick="send()">发送</button>
</div>
<div class="quickbar" id="quickbar">
 <a class="qbox" href="/form"><span class="qi">📋</span><span class="qt" id="qFormT">表单版</span><span class="qd" id="qFormD"></span></a>
 <a class="qbox" href="/model"><span class="qi">🧊</span><span class="qt" id="qModelT">模型分析</span><span class="qd" id="qModelD"></span></a>
 <button class="qbox" id="qLang" type="button" onclick="toggleLang()"><span class="qi">🌐</span><span class="qt" id="qRuT">俄语界面</span><span class="qd" id="qRuD"></span></button>
</div>
<script>
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function md(s){
 var L=esc(s).split(/\r?\n/),out=[],i=0;
 function inl(t){
  t=t.replace(/\[\[ok\]\]\s*(.*?)\s*\[\[\/ok\]\]/g,'<span class="ok">$1</span>');
  t=t.replace(/\[\[bad\]\]\s*(.*?)\s*\[\[\/bad\]\]/g,'<span class="bad">$1</span>');
  t=t.replace(/\[\[mask\]\]\s*(.*?)\s*\[\[\/mask\]\]/g,'<span class="mask">$1</span>');
  t=t.replace(/\[\[warn\]\]\s*(.*?)\s*\[\[\/warn\]\]/g,'<span class="warn">$1</span>');
  t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
  t=t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/([\u0400-\u04FF]+)/g,'<span class="ru">$1</span>');
  return t;
 }
 while(i<L.length){var t=L[i].trim();
  if(t===''){i++;continue}
  if(/^:::\s*na/i.test(t)){
   var title=L[i].replace(/^:::\s*na\s*/i,'');var body=[];i++;
   while(i<L.length&&!/^:::/.test(L[i].trim())){var b=L[i].trim();if(b)body.push(inl(b));i++}
   if(i<L.length&&/^:::$/.test(L[i].trim()))i++;
   out.push('<details class="na"><summary>'+inl(title||'未评项')+'</summary><div class="na-body">'+body.join('<br>')+'</div></details>');
   continue}
  if(/^[-*]\s+/.test(t)){var li=[];while(i<L.length&&/^[-*]\s+/.test(L[i].trim())){li.push('<li>'+inl(L[i].trim().replace(/^[-*]\s+/,''))+'</li>');i++}out.push('<ul>'+li.join('')+'</ul>');continue}
  if(/^>\s?/.test(t)){out.push('<blockquote>'+inl(t.replace(/^>\s?/,''))+'</blockquote>');i++;continue}
  if(/^###\s+/.test(t)||/^##\s+/.test(t)){out.push('<h3>'+inl(t.replace(/^#+\s+/,''))+'</h3>');i++;continue}
  out.push('<p>'+inl(t)+'</p>');i++}
 return out.join('');
}
function add(cls,html){var d=document.createElement('div');d.className='row '+cls;d.innerHTML='<div class="bub">'+html+'</div>';var m=document.getElementById('msgs');m.appendChild(d);m.scrollTop=m.scrollHeight;return d}
var HISTORY=[];
// ---------------- 界面语言（中 / 俄） ----------------
var LANG=(function(){try{return localStorage.getItem('dsh_lang')||'zh'}catch(e){return 'zh'}})();
var I18N={
 zh:{title:'🔥 中俄消防管材合规 · 智能问答',form:'← 表单版',terms:'⚖ 免责声明',lang:'Рус',notice:'本站仅作学习交流使用，不作为商业宣传或服务承诺',
  ph:'输入管材参数或问题，例如：DN100 镀锌管 Q235B 壁厚4.5 水压2.5',send:'发送',waiting:'检索中…',
  expand:'展开完整集体评价报告',collapse:'收起完整报告',fail:'失败：',
  chips:['镀锌管DN100示例','不锈钢304示例','查ГОСТ3262壁厚','查GB3091镀锌层','查032适用范围'],
  q_form:'表单版',q_form_d:'逐项填参数，出中俄对照报告',
  q_model:'模型分析',q_model_d:'上传 IFC 模型，三维标注比对',
  q_ru:'俄语界面',q_ru_d:'切换中文 / Русский',
  greet:'你好 👋 我是**中俄消防管材合规助手**，直接问我话就行：\n\n- **要我评价管子** → 给参数，例如 `DN100 镀锌管 Q235B 壁厚4.5 水压2.5`\n- **问标准要求** → 例如 `ГОСТ 3262 壁厚要求`、`GB/T 3091 镀锌层`\n- **问认证/法规** → 例如 `消防管要不要 EAC 认证`\n\n下方有示例，点一下就能试；也可以直接打字。\n\n输入框下方的三个方框可以随时切换到 **表单版 / 模型分析 / 俄语界面**。'},
 ru:{title:'🔥 Соответствие стальных труб КНР/РФ · ИИ-консультант',form:'← Форма',terms:'⚖ Отказ',lang:'中文',notice:'Сайт предназначен исключительно для учебного обмена опытом, а не для коммерческой рекламы или услуг',
  ph:'Введите параметры трубы или вопрос, напр.: DN100 оцинк. Q235B стенка 4.5 давление 2.5',send:'Отправить',waiting:'Поиск…',
  expand:'Показать полный отчёт',collapse:'Свернуть отчёт',fail:'Ошибка: ',
  chips:['Пример: оцинк. DN100','Пример: нерж. 304','ГОСТ 3262 толщина стенки','GB/T 3091 цинк. покрытие','ТР ТС 032 область применения'],
  q_form:'Форма',q_form_d:'Заполнить параметры → отчёт КНР/РФ',
  q_model:'Анализ модели',q_model_d:'Загрузить IFC, 3D-разметка',
  q_ru:'Русский интерфейс',q_ru_d:'Переключить 中文 / Русский',
  greet:'Здравствуйте 👋 Я **ИИ-консультант по соответствию стальных труб КНР/РФ**. Спрашивайте напрямую:\n\n- **Оценить трубу** → дайте параметры, напр. `DN100 оцинк. Q235B стенка 4.5 давление 2.5`\n- **Требования стандартов** → напр. `ГОСТ 3262 толщина стенки`\n- **Сертификация / регламенты** → напр. `нужен ли EAC`\n\nНиже есть примеры — нажмите, чтобы попробовать.\n\nТри кнопки под полем ввода — **Форма / Анализ модели / Русский интерфейс**.'}};
var QUERIES=['DN100 镀锌管 Q235B 壁厚4.5 水压2.5 抗拉400 屈服245 伸长率26',
 '不锈钢无缝管 06Cr19Ni10 DN100 外径114 壁厚4.5 水压2.5 抗拉520 屈服205 伸长率35',
 'ГОСТ 3262 壁厚要求','GB/T 3091 镀锌层要求','ТР ТС 032 适用范围'];
function T(k){return I18N[LANG][k]}
function renderStatic(){
 document.getElementById('hdTitle').textContent=T('title');
 document.getElementById('formLink').textContent=T('form');
 document.getElementById('termsLink').textContent=T('terms');
 document.getElementById('langBtn').textContent=T('lang');
 var nt=document.getElementById('notice'); if(nt) nt.textContent=T('notice');
 document.getElementById('q').placeholder=T('ph');
 document.getElementById('sendBtn').textContent=T('send');
 // 输入框下方的三个方框快捷方式
 if(document.getElementById('qFormT')){
  document.getElementById('qFormT').textContent=T('q_form');
  document.getElementById('qFormD').textContent=T('q_form_d');
  document.getElementById('qModelT').textContent=T('q_model');
  document.getElementById('qModelD').textContent=T('q_model_d');
  document.getElementById('qRuT').textContent=T('q_ru');
  document.getElementById('qRuD').textContent=T('q_ru_d');
  document.getElementById('qLang').classList.toggle('on',LANG==='ru');
 }
 var cb=document.getElementById('chips');cb.innerHTML='';
 T('chips').forEach(function(label,i){var c=document.createElement('span');c.className='chip';c.textContent=label;
  c.onclick=function(){document.getElementById('q').value=QUERIES[i];send()};cb.appendChild(c)});
}
function toggleLang(){LANG=(LANG==='zh'?'ru':'zh');try{localStorage.setItem('dsh_lang',LANG)}catch(e){}
 renderStatic();document.getElementById('msgs').innerHTML='';HISTORY=[];add('bot',md(T('greet')));}
function fill(t){document.getElementById('q').value=t;send()}
function chips(list){
 if(!list||!list.length)return;
 var bar=document.createElement('div');bar.className='chips';bar.style.padding='2px 0 8px';
 list.forEach(function(s){var c=document.createElement('span');c.className='chip';c.textContent=s;c.onclick=function(){fill(s)};bar.appendChild(c)});
 document.getElementById('msgs').appendChild(bar);document.getElementById('msgs').scrollTop=1e9;
}
async function send(){
 var q=document.getElementById('q');var t=q.value.trim();if(!t)return;q.value='';
 add('me',esc(t));HISTORY.push({role:'user',content:t});
 var wait=add('bot','<i>'+T('waiting')+'</i>');
 try{
  var r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:t,history:HISTORY.slice(-6),lang:LANG})});
  var d=await r.json();
  var html=md(d.reply||'');
  if(d.report){html+='<div class="rep" style="display:none"></div>';}
  wait.querySelector('.bub').innerHTML=html;
  if(d.report){var rep=wait.querySelector('.rep');rep.innerHTML=md(d.report);
   var btn=document.createElement('button');btn.textContent=T('expand');
   btn.style.cssText='margin-top:6px;padding:6px 12px;border:1px solid #9dc1ff;background:#fff;color:#1f6feb;border-radius:8px;cursor:pointer';
   btn.onclick=function(){var open=rep.style.display==='none';rep.style.display=open?'block':'none';btn.textContent=open?T('collapse'):T('expand')};
   wait.querySelector('.bub').appendChild(btn);}
  HISTORY.push({role:'bot',content:d.reply||''});
  chips(d.followups);
 }catch(e){wait.querySelector('.bub').innerHTML='<span class="bad">'+T('fail')+esc(e.message)+'</span>'}
}
renderStatic();
add('bot',md(T('greet')));
</script></body></html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
