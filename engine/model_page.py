# -*- coding: utf-8 -*-
"""模型分析页面（IFC 管材语义分析 + 3D 可视化标注对比）。

零外部依赖：3D 用自写正交投影 + 画家算法在 2D canvas 上渲染管段圆柱，
不引入 three.js / web-ifc，避免 CDN 在境内或俄罗斯被墙、也免去大体积 WASM。
"""

MODEL_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>模型分析 · 中俄消防金属管材合规</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;background:#eef1f6;color:#1f2430;margin:0;padding:0}
.top{background:#fff;border-bottom:1px solid #e5e7eb;padding:12px 20px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;flex-wrap:wrap}
.top h1{font-size:16px;margin:0;color:#0b1f4d;font-weight:700}
.top .nav{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top a{color:#1f6feb;text-decoration:none;font-size:13px;padding:5px 11px;border-radius:14px;border:1px solid #d7e3f7}
.top a:hover{background:#eef3fb}
.top a.on{background:#1f6feb;color:#fff;border-color:#1f6feb}
.langbtn{background:#1f6feb;color:#fff;border:0;border-radius:14px;padding:5px 13px;font-size:12.5px;cursor:pointer;font-weight:600}
.wrap{max-width:1560px;margin:0 auto;padding:16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(16,24,40,.06)}
#drop{border:2px dashed #b9c8de;border-radius:12px;padding:30px 16px;text-align:center;color:#5a6a80;cursor:pointer;background:#fafbfe;transition:.15s}
#drop:hover,#drop.hot{border-color:#1f6feb;background:#eef3fb;color:#1f6feb}
#drop b{display:block;font-size:15px;margin-bottom:6px;color:#0b1f4d}
#drop span{font-size:12.5px;color:#8b97a8}
.capbadge{display:none;margin-top:10px !important;background:#eef3fb;border:1px solid #d7e3f7;
  color:#1f6feb !important;font-weight:700;font-size:12.5px !important;border-radius:10px;
  padding:4px 13px;width:fit-content;margin-left:auto;margin-right:auto}
.capbadge.on{display:inline-block}
.stat{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 12px}
.stat div{background:#f6f8fc;border:1px solid #e5eaf3;border-radius:9px;padding:8px 13px;font-size:12.5px;color:#5a6a80}
.stat b{display:block;font-size:19px;color:#0b1f4d;font-weight:700;line-height:1.25}
.legend{display:flex;gap:13px;flex-wrap:wrap;font-size:12.5px;color:#5a6a80;align-items:center}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.limits{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:11px;font-size:12px;color:#5a6a80}
.limits>span{background:#f6f8fc;border:1px solid #e5eaf3;border-radius:9px;padding:5px 11px;line-height:1.5}
.limits b{color:#0b1f4d}
.limits .cap{background:#eef3fb;border-color:#d7e3f7;color:#1f6feb;font-weight:700}
.limits .cap b{color:#1f6feb}
.work{display:grid;grid-template-columns:300px minmax(0,1fr) 340px;gap:14px;align-items:start}
@media(max-width:1250px){.work{grid-template-columns:1fr}}
.panel{background:#fff;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 1px 3px rgba(16,24,40,.06);overflow:hidden}
.panel h3{margin:0;padding:11px 14px;font-size:13.5px;color:#0b1f4d;background:#f6f8fc;border-bottom:1px solid #e5eaf3;font-weight:700}
.plist{max-height:640px;overflow:auto}
.pitem{padding:10px 13px;border-bottom:1px solid #f0f2f7;cursor:pointer;font-size:13px;display:flex;gap:9px;align-items:flex-start}
.pitem:hover{background:#f7f9fd}
.pitem.on{background:#eef3fb;box-shadow:inset 3px 0 0 #1f6feb}
.dot{width:10px;height:10px;border-radius:50%;flex:0 0 10px;margin-top:5px}
.pitem .nm{font-weight:600;color:#1f2430;line-height:1.35}
.pitem .sub{font-size:11.5px;color:#8b97a8;margin-top:3px;line-height:1.4}
#viewbox{position:relative;background:#f7f9fc}
canvas{display:block;width:100%;height:640px;cursor:grab}
canvas.drag{cursor:grabbing}
#tools{position:absolute;left:10px;top:10px;display:flex;gap:6px;flex-wrap:wrap}
#tools button{background:rgba(255,255,255,.94);border:1px solid #d7e0ee;border-radius:7px;padding:5px 10px;font-size:12px;cursor:pointer;color:#31425c}
#tools button:hover{background:#fff;border-color:#1f6feb;color:#1f6feb}
#tools button.on{background:#1f6feb;color:#fff;border-color:#1f6feb}
#tip{position:absolute;right:10px;bottom:10px;background:rgba(255,255,255,.94);border:1px solid #d7e0ee;border-radius:8px;padding:7px 11px;font-size:11.5px;color:#5a6a80;max-width:330px;line-height:1.5}
#detail{padding:13px;font-size:13px;max-height:640px;overflow:auto}
#detail .kv{display:grid;grid-template-columns:96px 1fr;gap:4px 9px;font-size:12.5px;margin-bottom:9px}
#detail .kv .k{color:#8b97a8}
#detail .kv .v{color:#1f2430;word-break:break-all}
.tags span{display:inline-block;font-size:11px;padding:2px 7px;border-radius:9px;margin:0 4px 4px 0;background:#eef3fb;color:#1f6feb;border:1px solid #d7e3f7}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{border:1px solid #e5eaf3;padding:7px 9px;text-align:left;vertical-align:top}
th{background:#eef3fb;color:#0b1f4d;font-weight:600;white-space:nowrap}
tr:nth-child(even) td{background:#fafbfe}
.spec-card{border:1px solid #e5e7eb;border-radius:11px;margin-bottom:12px;overflow:hidden}
.spec-head{padding:11px 14px;display:flex;gap:11px;align-items:center;background:#f6f8fc;border-bottom:1px solid #e5eaf3;cursor:pointer;flex-wrap:wrap}
.spec-head .lbl{font-weight:700;color:#0b1f4d;font-size:14px}
.spec-head .meta{font-size:11.5px;color:#8b97a8;margin-left:auto;text-align:right;line-height:1.45}
.spec-body{padding:13px 14px;display:none}
.spec-body.open{display:block}
.sumtxt{background:#f3f6fc;border-left:4px solid #1f6feb;border-radius:0 8px 8px 0;padding:9px 13px;font-size:13px;color:#46536b;line-height:1.7;margin:0 0 11px}
.pill{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:10px;margin-right:6px}
.pill.pass{background:#e6f5ea;color:#16772f} .pill.fail{background:#fdeaeb;color:#c0262f}
.pill.warn{background:#fdf0e2;color:#b35f10} .pill.na{background:#f0f1f4;color:#6b7280}
.pill.unknown{background:#e8f1fe;color:#1f6feb}
.ok{color:#16772f;font-weight:700} .bad{color:#c0262f;font-weight:700} .warnc{color:#e67e22;font-weight:700}
@media(max-width:760px){canvas{height:420px}}
</style>
</head>
<body>
<div class="top">
  <h1 data-i18n="t">模型分析 · 中俄消防金属管材合规</h1>
  <div class="nav">
    <a href="/form" data-i18n="nav_form">表单版</a>
    <a href="/chat" data-i18n="nav_chat">问答版</a>
    <a href="/model" class="on" data-i18n="nav_model">模型分析</a>
    <a href="/terms" data-i18n="nav_terms">免责声明</a>
    <button class="langbtn" id="langBtn" onclick="toggleLang()">RU</button>
  </div>
</div>
<div class="wrap">

  <div class="card" id="upcard">
    <div id="drop">
      <b data-i18n="up1">把 BIM 模型导出的 IFC 文件拖到这里</b>
      <span data-i18n="up2">或点击选择文件（.ifc，IFC2X3 / IFC4 均可；Revit：文件 → 导出 → IFC）</span>
      <span class="capbadge" id="capBadge"></span>
      <input type="file" id="file" accept=".ifc,.IFC" style="display:none">
    </div>
    <div id="limits" class="limits"></div>
    <div id="status" style="margin-top:11px;font-size:13px;color:#5a6a80"></div>
    <div class="legend" style="margin-top:11px">
      <span><i style="background:#16772f"></i><span data-i18n="lg_pass">通过</span></span>
      <span><i style="background:#c0262f"></i><span data-i18n="lg_fail">不通过</span></span>
      <span><i style="background:#e67e22"></i><span data-i18n="lg_warn">边缘／数据偏少</span></span>
      <span><i style="background:#9aa3af"></i><span data-i18n="lg_na">评估不足，未定级</span></span>
      <span><i style="background:#1f6feb"></i><span data-i18n="lg_unknown">模型信息不足，待确认</span></span>
    </div>
  </div>

  <div id="out" style="display:none">
    <div class="card">
      <div class="stat" id="stats"></div>
      <div class="legend" id="colors"></div>
    </div>

    <div class="work">
      <div class="panel">
        <h3 data-i18n="h_specs">管材规格清单</h3>
        <div class="plist" id="plist"></div>
      </div>
      <div class="panel">
        <h3 data-i18n="h_view">三维识别与标注</h3>
        <div id="viewbox">
          <div id="tools">
            <button onclick="resetView()" data-i18n="b_reset">重置视角</button>
            <button onclick="fitView()" data-i18n="b_fit">适应窗口</button>
            <button id="b_lbl" onclick="toggleLabels()" data-i18n="b_lbl">显示标签</button>
            <button id="b_bad" onclick="toggleOnlyBad()" data-i18n="b_bad">只看不达标</button>
            <button onclick="topView()" data-i18n="b_top">俯视</button>
            <button onclick="frontView()" data-i18n="b_front">正立面</button>
          </div>
          <canvas id="view" width="1200" height="640"></canvas>
          <div id="tip" data-i18n="tip">左键拖拽旋转 · 滚轮缩放 · 右键拖拽平移 · 点击管段查看该构件的指标判定</div>
        </div>
      </div>
      <div class="panel">
        <h3 data-i18n="h_detail">构件详情</h3>
        <div id="detail"><p style="color:#8b97a8;font-size:12.5px" data-i18n="d_none">点击三维视图中的管段或左侧规格查看详情</p></div>
      </div>
    </div>

    <div class="card">
      <div style="font-weight:700;color:#0b1f4d;font-size:15px;margin-bottom:4px" data-i18n="h_concl">集体评价结论（与表单版同口径）</div>
      <div style="font-size:12px;color:#8b97a8;margin-bottom:12px" data-i18n="h_concl_sub">逐条引用中国 / 俄罗斯标准条款；未评项如实标注为「评估不足」，不臆造数据。</div>
      <div id="concl"></div>
    </div>
  </div>
</div>

<script>
/* ============================ i18n ============================ */
var I18N = {
 "t":["模型分析 · 中俄消防金属管材合规","Анализ BIM-модели · соответствие труб КНР и РФ"],
 "nav_form":["表单版","Форма"],"nav_chat":["问答版","Чат"],"nav_model":["模型分析","Модель"],"nav_terms":["免责声明","Отказ от ответственности"],
 "up1":["把 BIM 模型导出的 IFC 文件拖到这里","Перетащите сюда файл IFC, экспортированный из BIM"],
 "up2":["或点击选择文件（.ifc，IFC2X3 / IFC4 均可；Revit：文件 → 导出 → IFC）","или нажмите для выбора файла (.ifc, IFC2X3 / IFC4; Revit: Файл → Экспорт → IFC)"],
 "lg_pass":["通过","Соответствует"],"lg_fail":["不通过","Не соответствует"],"lg_warn":["边缘／数据偏少","На границе / мало данных"],
 "lg_na":["评估不足，未定级","Недостаточно данных"],"lg_unknown":["模型信息不足，待确认","Мало данных в модели"],
 "h_specs":["管材规格清单","Перечень типоразмеров"],
 "h_view":["三维识别与标注","3D-идентификация и разметка"],
 "h_detail":["构件详情","Сведения об элементе"],
 "h_concl":["集体评价结论（与表单版同口径）","Итоговое заключение (та же методика, что и в форме)"],
 "h_concl_sub":["逐条引用中国 / 俄罗斯标准条款；未评项如实标注为「评估不足」，不臆造数据。","Пункты стандартов КНР и РФ приводятся дословно; при нехватке данных указывается «недостаточно данных»."],
 "b_reset":["重置视角","Сброс"],"b_fit":["适应窗口","Вписать"],"b_lbl":["显示标签","Подписи"],
 "b_bad":["只看不达标","Только несоответствия"],"b_top":["俯视","Сверху"],"b_front":["正立面","Фасад"],
 "tip":["左键拖拽旋转 · 滚轮缩放 · 右键拖拽平移 · 点击管段查看该构件的指标判定","ЛКМ — вращение · колесо — масштаб · ПКМ — сдвиг · клик по трубе — показатели элемента"],
 "d_none":["点击三维视图中的管段或左侧规格查看详情","Выберите трубу в 3D-виде или типоразмер слева"],
 "parsing":["正在解析模型…","Обработка модели…"],"reading":["正在读取文件…","Чтение файла…"],
 "fail_":["解析失败","Ошибка обработки"],
 "s_el":["管道构件","Элементов"],"s_sp":["管材规格","Типоразмеров"],"s_judged":["参与判定","Оценено"],
 "s_unk":["信息不足待确认","Мало данных"],"s_cov":["数据覆盖率","Полнота данных"],
 "c_spec":["规格","Типоразмер"],"c_mat":["材质","Материал"],"c_std":["适用标准","Стандарты"],
 "c_cn":["中国侧","КНР"],"c_ru":["俄罗斯侧","РФ"],"c_verdict":["结论","Заключение"],
 "c_items":["逐条指标","Показатели"],"c_ind":["指标","Показатель"],"c_val":["产品值","Значение"],
 "c_req":["要求","Требование"],"c_clause":["条款","Пункт"],"c_verdict2":["判定","Оценка"],
 "c_el":["构件","Элементов"],"expand":["展开逐条指标","Показать показатели"],"collapse":["收起","Свернуть"],
 "assumed":["类型为推断值，结论仅供参考","Тип определён предположительно"],
 "v_pass":["通过","Соответствует"],"v_fail":["不通过","Не соответствует"],
 "v_na":["评估不足","Недостаточно данных"],
 "storey":["楼层","Этаж"],"type":["类型","Тип"],"props":["模型属性","Свойства модели"],
 "geom_none":["该构件无实体几何，仅按放置点定位","Нет геометрии, только точка размещения"],
 "err_size":["文件过大：当前服务上限 64MB（请只导出管道相关构件）","Файл слишком большой: лимит 64 МБ (экспортируйте только трубопроводы)"],
 "lim_cap":["最大导入","Макс. размер файла"],
 "lim_mem":["服务器可用内存","Доступно памяти"],
 "lim_noswap":["无 swap","без swap"],
 "lim_peak":["解析峰值内存","Пиковая память"],
 "lim_disk":["磁盘可用","Свободно на диске"],
 "lim_est":["预计占用","Ожидается"],
 "lim_note":["解析峰值内存约为文件大小的 12~15 倍，瓶颈是内存；请只导出管道相关构件（隔离管道/管件/管道附件后再导出）以显著减小文件。",
             "Пиковая память примерно в 12–15 раз больше файла; экспортируйте только трубопроводы."],
 "no_geom":["模型中没有识别到管道构件","В модели не найдены трубопроводы"],
 "idx":["中国侧评级","Оценка КНР"]
};
var LANG = localStorage.getItem('dsh_lang') || 'zh';
function T(k){var v=I18N[k];return v?(v[LANG==='ru'?1:0]||v[0]):k;}
function applyLang(){
  document.documentElement.lang = LANG==='ru'?'ru':'zh';
  document.getElementById('langBtn').textContent = LANG==='ru'?'中文':'RU';
  var els=document.querySelectorAll('[data-i18n]');
  for(var i=0;i<els.length;i++){els[i].textContent=T(els[i].getAttribute('data-i18n'));}
  if(DATA){renderStats();renderList();renderConcl();}
  if(LIMITS){renderLimits();}
}
function toggleLang(){LANG = LANG==='ru'?'zh':'ru';localStorage.setItem('dsh_lang',LANG);applyLang();if(SEL)showDetail(SEL);}

/* ============================ 状态 ============================ */
var DATA=null, SEL=null, SHOW_LABELS=false, ONLY_BAD=false, LIMITS=null;
var COLORS={pass:'#16772f',fail:'#c0262f',warn:'#e67e22',na:'#9aa3af',unknown:'#1f6feb'};
var V={yaw:-0.62,pitch:0.46,scale:1,ox:0,oy:0};
var GEOM=[],CEN=[0,0,0],EX=[1,1];

/* ======================= 导入上限 / 内存水位 ======================= */
function loadLimits(){
  fetch('/api/model/limits').then(function(r){return r.json();}).then(function(d){
    LIMITS=d; renderLimits();
  }).catch(function(){});
}
function renderLimits(){
  var box=document.getElementById('limits'); if(!box) return;
  var d=LIMITS;
  if(!d){ box.innerHTML=''; return; }
  // 导入区正中间的"最大 XX MB"徽标
  var badge=document.getElementById('capBadge');
  if(badge){
    badge.textContent=T('lim_cap')+' '+d.max_mb+' MB';
    badge.classList.add('on');
  }
  var h='<span class="cap">'+T('lim_cap')+' <b>'+d.max_mb+' MB</b></span>';
  if(d.mem_available_mb){
    h+='<span>'+T('lim_mem')+' <b>'+(d.mem_available_mb/1024).toFixed(1)+' GB</b> / '+
       (d.mem_total_mb/1024).toFixed(1)+' GB'+(d.mem_swap_mb?'':' · '+T('lim_noswap'))+'</span>';
  }
  h+='<span>'+T('lim_peak')+' <b>×'+d.peak_factor+'</b>　'+d.max_mb+' MB → ≈'+
     (d.peak_mb_at_max/1024).toFixed(1)+' GB</span>';
  if(d.disk_free_mb){
    h+='<span>'+T('lim_disk')+' <b>'+(d.disk_free_mb/1024).toFixed(0)+' GB</b></span>';
  }
  h+='<span style="flex-basis:100%;background:none;border:0;padding:0;color:#8b97a8">'+T('lim_note')+'</span>';
  box.innerHTML=h;
}

/* ============================ 上传 ============================ */
var drop=document.getElementById('drop'),fi=document.getElementById('file'),st=document.getElementById('status');
drop.onclick=function(){fi.click();};
drop.ondragover=function(e){e.preventDefault();drop.classList.add('hot');};
drop.ondragleave=function(){drop.classList.remove('hot');};
drop.ondrop=function(e){e.preventDefault();drop.classList.remove('hot');
  if(e.dataTransfer.files&&e.dataTransfer.files.length)send(e.dataTransfer.files[0]);};
fi.onchange=function(){if(fi.files.length)send(fi.files[0]);};

function send(f){
  if(!/\.ifc$/i.test(f.name)){if(!confirm('文件扩展名不是 .ifc，仍要尝试解析吗？'))return;}
  var maxb=(LIMITS&&LIMITS.max_bytes)?LIMITS.max_bytes:64*1024*1024;
  var fac=(LIMITS&&LIMITS.peak_factor)?LIMITS.peak_factor:15;
  var mb=f.size/1048576;
  if(f.size>maxb){st.innerHTML='<span class="bad">'+T('err_size')+'</span>';return;}
  st.innerHTML='<span class="warnc">'+T('reading')+' '+mb.toFixed(1)+' MB · '+T('lim_est')+
               ' ≈'+(mb*fac).toFixed(0)+' MB</span>';
  var xhr=new XMLHttpRequest();
  xhr.open('POST','/api/model/analyze?filename='+encodeURIComponent(f.name));
  xhr.upload.onprogress=function(e){
    if(e.lengthComputable)st.innerHTML='<span class="warnc">'+T('reading')+' '+(e.loaded/1048576).toFixed(1)+'/'+(e.total/1048576).toFixed(1)+' MB</span>';
  };
  xhr.onload=function(){
    if(xhr.status!==200){
      var msg=T('fail_');
      try{msg+='：'+(JSON.parse(xhr.responseText).detail||xhr.statusText);}catch(e){msg+='：'+xhr.statusText;}
      st.innerHTML='<span class="bad">'+msg+'</span>';return;
    }
    st.innerHTML='<span class="ok">'+T('parsing')+'</span>';
    try{DATA=JSON.parse(xhr.responseText);}catch(e){st.innerHTML='<span class="bad">'+T('fail_')+'：'+e+'</span>';return;}
    build();st.innerHTML='';
  };
  xhr.onerror=function(){st.innerHTML='<span class="bad">'+T('fail_')+'</span>';};
  st.innerHTML='<span class="warnc">'+T('parsing')+'</span>';
  xhr.send(f);
}

/* ============================ 构建 ============================ */
function build(){
  document.getElementById('out').style.display='block';
  GEOM=[];
  var els=DATA.elements||[];
  for(var i=0;i<els.length;i++){
    var e=els[i];
    if(!e.geom)continue;
    var g={id:e.id,spec:e.spec,color:COLORS[e.color]||'#9aa3af',label:(e.type_name||e.name||('#'+e.id))};
    if(e.geom.k==='cyl'){g.p0=e.geom.p0;g.p1=e.geom.p1;g.r=Math.max(e.geom.r,3);}
    else{g.p=e.geom.p;g.pt=true;g.r=Math.max(18,0);}
    GEOM.push(g);
  }
  renderStats();renderList();renderConcl();
  requestAnimationFrame(function(){resize();fitView();draw();});
}

function renderStats(){
  var t=DATA.totals||{},m=DATA.model||{};
  var cov=[];
  if(t.cn&&t.cn.coverage!=null)cov.push('CN '+(t.cn.coverage*100).toFixed(0)+'%');
  if(t.ru&&t.ru.coverage!=null)cov.push('RU '+(t.ru.coverage*100).toFixed(0)+'%');
  document.getElementById('stats').innerHTML=
    '<div><b>'+(t.elements||0)+'</b>'+T('s_el')+'</div>'+
    '<div><b>'+(t.specs||0)+'</b>'+T('s_sp')+'</div>'+
    '<div><b>'+(t.judged_specs||0)+'</b>'+T('s_judged')+'</div>'+
    '<div><b>'+(t.unknown_specs||0)+'</b>'+T('s_unk')+'</div>'+
    '<div><b>'+(cov.length?cov.join(' / '):'—')+'</b>'+T('s_cov')+'</div>'+
    '<div><b>'+(m.schema||'—')+'</b>schema</div>'+
    '<div><b>'+(m.file||'—')+'</b>file</div>';
  var c=t.spec_color||{},html='';
  for(var k in c){html+='<span><i style="background:'+(COLORS[k]||'#9aa3af')+'"></i>'+T('lg_'+k)+'：'+c[k]+'</span>';}
  document.getElementById('colors').innerHTML=html;
}

function renderList(){
  var box=document.getElementById('plist'),h='';
  var specs=DATA.specs||[];
  for(var i=0;i<specs.length;i++){
    var s=specs[i];
    h+='<div class="pitem" data-k="'+s.key+'" onclick="pick(\''+s.key+'\')">'+
       '<span class="dot" style="background:'+(COLORS[s.color]||'#9aa3af')+'"></span><span style="flex:1">'+
       '<div class="nm">'+esc(s.label)+'</div>'+
       '<div class="sub">'+esc(s.key)+' · '+(s.type_name?esc(s.type_name)+' · ':'')+
       (s.count||0)+(LANG==='ru'?' шт.':' 件')+'</div></span></div>';
  }
  box.innerHTML=h||'<div style="padding:14px;color:#8b97a8;font-size:12.5px">'+T('no_geom')+'</div>';
}

function renderConcl(){
  var box=document.getElementById('concl'),h='';
  var specs=DATA.specs||[];
  for(var i=0;i<specs.length;i++){
    var s=specs[i],r=s.result||{},idx=r.index||{},p=s.product||{};
    h+='<div class="spec-card"><div class="spec-head" onclick="this.nextSibling.classList.toggle(\'open\')">'+
       '<span class="dot" style="background:'+(COLORS[s.color]||'#9aa3af')+'"></span>'+
       '<span class="lbl">'+esc(s.key)+' · '+esc(s.label)+'</span>'+
       '<span class="pill '+(s.color||'na')+'">'+T('lg_'+(s.color||'na'))+'</span>'+
       (p.type_source==='default'?'<span class="pill unknown">'+T('assumed')+'</span>':'')+
       '<span class="meta">'+esc(p.material_grade_cn||'—')+' / '+esc(p.material_grade_ru||'—')+
       '<br>'+(s.count||0)+(LANG==='ru'?' шт.':' 件')+'</span></div>'+
       '<div class="spec-body">';
    if(s.note)h+='<div class="sumtxt" style="border-color:#1f6feb">'+esc(s.note)+'</div>';
    if(r.overall&&r.overall.summary)h+='<div class="sumtxt">'+esc(r.overall.summary)+'</div>';
    h+='<table style="margin-bottom:11px"><tr><th>'+T('c_std')+'</th><th>'+T('c_verdict')+'</th><th>'+T('c_el')+'</th></tr>';
    h+='<tr><td>CN：'+esc((r.cn_results||[]).map(function(x){return x.standard;}).join('、')||'—')+'</td>'+
       '<td>'+verdictPill(idx.cn)+'</td><td>'+(s.count||0)+'</td></tr>';
    h+='<tr><td>RU：'+esc((r.ru_results||[]).map(function(x){return x.standard;}).join('、')||'—')+'</td>'+
       '<td>'+verdictPill(idx.ru)+'</td><td>'+(s.count||0)+'</td></tr></table>';
    h+=itemsTable(r);
    h+='</div></div>';
  }
  box.innerHTML=h||'<p style="color:#8b97a8">'+T('no_geom')+'</p>';
}

function verdictPill(si){
  if(!si)return '—';
  var g=si.grade||'NA',cls=(g==='A'||g==='B')?'pass':(g==='NA'?'na':'fail');
  var lab=(g==='A'||g==='B')?T('v_pass'):(g==='NA'?T('v_na'):T('v_fail'));
  var pn=(si.passed||0)+(si.failed||0);
  return '<span class="pill '+cls+'">'+lab+'</span><span style="font-size:11.5px;color:#8b97a8">'+
         (g!=='NA'?('评级 '+g+' · '):'')+'可评 '+pn+' 项 / 通过 '+(si.passed||0)+
         ' · 覆盖率 '+((si.coverage!=null)?(si.coverage*100).toFixed(0)+'%':'—')+'</span>';
}

function itemsTable(r){
  var rows='',n=0;
  ['cn_results','ru_results'].forEach(function(side){
    (r[side]||[]).forEach(function(std){
      (std.items||[]).forEach(function(it){
        var cls=it.verdict==='pass'?'ok':(it.verdict==='fail'?'bad':'');n++;
        rows+='<tr><td>'+esc(std.standard)+'</td><td>'+esc(it.indicator_zh||it.indicator_id)+
              '<div style="color:#8b97a8;font-size:11px">'+esc(it.indicator_id)+'</div></td>'+
              '<td class="'+cls+'">'+esc(fmtv(it.product_value))+' '+esc(it.unit||'')+'</td>'+
              '<td>'+esc(fmtv(it.requirement))+'</td>'+
              '<td>'+esc(it.clause||'—')+'</td>'+
              '<td class="'+cls+'">'+esc(it.verdict||'')+'</td></tr>';
      });
    });
  });
  if(!n)return '';
  return '<div style="font-size:12px;color:#8b97a8;margin:6px 0 5px">'+T('c_items')+'（'+n+'）</div>'+
    '<table><tr><th>'+T('c_std')+'</th><th>'+T('c_ind')+'</th><th>'+T('c_val')+'</th><th>'+T('c_req')+
    '</th><th>'+T('c_clause')+'</th><th>'+T('c_verdict2')+'</th></tr>'+rows+'</table>';
}
function fmtv(v){
  if(v===null||v===undefined)return '—';
  if(typeof v==='object'){try{return JSON.stringify(v);}catch(e){return String(v);}}
  return String(v);
}
function esc(s){return String(s===null||s===undefined?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

/* ============================ 三维渲染 ============================ */
var cv=document.getElementById('view'),ctx=cv.getContext('2d');
var DPR=window.devicePixelRatio||1;

function resize(){
  var w=cv.clientWidth||1200,h=cv.clientHeight||640;
  cv.width=w*DPR;cv.height=h*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
}
window.addEventListener('resize',function(){resize();draw();});

function cam(p){
  var cy=Math.cos(V.yaw),sy=Math.sin(V.yaw),cp=Math.cos(V.pitch),sp=Math.sin(V.pitch);
  var x=p[0]-CEN[0],y=p[1]-CEN[1],z=p[2]-CEN[2];
  var x1=cy*x+sy*y, y1=-sy*x+cy*y;
  var y2=cp*y1-sp*z, z2=sp*y1+cp*z;
  return [x1,y2,z2];
}
function proj(c,w,h){return [w/2+V.ox+c[0]*V.scale, h/2+V.oy-c[2]*V.scale, c[1]];}

function fitView(){
  if(!GEOM.length)return;
  var w=cv.clientWidth||1200,h=cv.clientHeight||640;
  // 世界质心（供相机变换使用）
  var n=0,sx=0,sy=0,sz=0;
  GEOM.forEach(function(g){
    var pts=g.pt?[g.p]:[g.p0,g.p1];
    pts.forEach(function(p){sx+=p[0];sy+=p[1];sz+=p[2];n++;});
  });
  CEN=[sx/n,sy/n,sz/n];

  // 取景用"稳健包围盒"而不是 min/max：
  // 真实 BIM 模型里常有极少数构件（注释、远离主体的构件）坐标差出上百米，
  // 用 min/max 会让整个模型被压成看不见的点（实测 scale 被压到 1/14）。
  // 做法：用 中位数 ± 5×MAD 剔除离群点后再取 min/max —— 只丢真正的离群点，不裁掉主体边缘。
  var xs=[],zs=[];
  GEOM.forEach(function(g){
    var pts=g.pt?[g.p]:[g.p0,g.p1];
    pts.forEach(function(p){var c=cam(p);xs.push(c[0]);zs.push(c[2]);});
  });
  if(!xs.length)return;
  function med(a){var n=a.length;if(!n)return 0;var m=n>>1;return n%2?a[m]:(a[m-1]+a[m])/2;}
  function mad(a){var mm=med(a),d=a.map(function(v){return Math.abs(v-mm);});d.sort(function(x,y){return x-y;});return med(d);}
  function robustRange(arr){
    var s=arr.slice().sort(function(a,b){return a-b;});
    var mm=med(s), d=mad(s);
    if(!(d>0))return [s[0],s[s.length-1]];
    var lo=mm-5*d, hi=mm+5*d;
    var keep=s.filter(function(v){return v>=lo&&v<=hi;});
    if(keep.length<2)return [s[0],s[s.length-1]];
    return [keep[0],keep[keep.length-1]];
  }
  var rx=robustRange(xs), rz=robustRange(zs);
  var cx=(rx[0]+rx[1])/2, cz=(rz[0]+rz[1])/2;
  var ex=Math.max((rx[1]-rx[0])/2,1e-6), ez=Math.max((rz[1]-rz[0])/2,1e-6);
  EX=[ex,ez];
  var s=Math.min(w*0.44/ex, h*0.44/ez);
  V.scale=isFinite(s)&&s>0?s:1;
  // 把稳健盒中心移到画布中心
  V.ox=-cx*V.scale;
  V.oy=cz*V.scale;
}

function draw(){
  var w=cv.clientWidth||1200,h=cv.clientHeight||640;
  ctx.clearRect(0,0,w,h);
  if(!GEOM.length){
    ctx.fillStyle='#9aa3af';ctx.font='14px system-ui';ctx.textAlign='center';
    ctx.fillText(T('no_geom'),w/2,h/2);return;
  }
  var items=[];
  for(var i=0;i<GEOM.length;i++){
    var g=GEOM[i];
    if(ONLY_BAD && (g.color===COLORS.pass || g.color===COLORS.na))continue;
    var o={g:g};
    if(g.pt){o.a=proj(cam(g.p),w,h);o.d=o.a[2];o.pt=true;}
    else{o.a=proj(cam(g.p0),w,h);o.b=proj(cam(g.p1),w,h);o.d=(o.a[2]+o.b[2])/2;}
    o.sel=(SEL&&g.spec===SEL);
    items.push(o);
  }
  items.sort(function(x,y){return y.d-x.d;});
  for(var j=0;j<items.length;j++)drawOne(items[j],w,h);
  if(SHOW_LABELS||SEL)drawLabels(items,w,h);
  drawGizmo(w,h);
}

function drawOne(o,w,h){
  var g=o.g;
  var lw=Math.max(1.6,(g.r||3)*2*V.scale);
  if(o.pt){
    ctx.fillStyle=g.color;
    ctx.beginPath();ctx.arc(o.a[0],o.a[1],Math.max(4,(g.r||18)*V.scale),0,6.284);ctx.fill();
    if(o.sel){ctx.strokeStyle='#0b1f4d';ctx.lineWidth=2.5;ctx.stroke();}
    return;
  }
  if(o.sel){ctx.strokeStyle='#0b1f4d';ctx.lineWidth=lw+5;ctx.lineCap='butt';
    ctx.beginPath();ctx.moveTo(o.a[0],o.a[1]);ctx.lineTo(o.b[0],o.b[1]);ctx.stroke();}
  ctx.strokeStyle=o.sel?'#ffd24d':g.color;ctx.lineWidth=lw;ctx.lineCap='butt';
  ctx.beginPath();ctx.moveTo(o.a[0],o.a[1]);ctx.lineTo(o.b[0],o.b[1]);ctx.stroke();
  // 端面圆
  ctx.fillStyle=o.sel?'#ffd24d':g.color;
  ctx.beginPath();ctx.arc(o.a[0],o.a[1],lw/2,0,6.284);ctx.fill();
  ctx.beginPath();ctx.arc(o.b[0],o.b[1],lw/2,0,6.284);ctx.fill();
}

function drawLabels(items,w,h){
  ctx.font='11px system-ui';ctx.textAlign='left';ctx.textBaseline='middle';
  var shown=0;
  for(var i=0;i<items.length;i++){
    var o=items[i];if(o.pt)continue;
    var g=o.g;
    var isBad=(g.color===COLORS.fail);
    if(!(o.sel||(SHOW_LABELS&&(isBad||g.color===COLORS.warn))))continue;
    if(shown++>140)break;
    var mx=(o.a[0]+o.b[0])/2,my=(o.a[1]+o.b[1])/2;
    var txt=(g.spec||'')+' '+(g.label||'');
    if(txt.length>26)txt=txt.slice(0,26)+'…';
    var tw=ctx.measureText(txt).width;
    ctx.fillStyle='rgba(255,255,255,.9)';
    ctx.fillRect(mx+8,my-9,tw+10,18);
    ctx.strokeStyle=o.sel?'#0b1f4d':'#d7e0ee';ctx.lineWidth=1;
    ctx.strokeRect(mx+8,my-9,tw+10,18);
    ctx.fillStyle=o.sel?'#0b1f4d':'#31425c';
    ctx.fillText(txt,mx+13,my);
  }
}

function drawGizmo(w,h){
  var ox=w-56,oy=h-46,L=26;
  var ax=[[1,0,0,'X','#c0262f'],[0,1,0,'Y','#16772f'],[0,0,1,'Z','#1f6feb']];
  for(var i=0;i<3;i++){
    var a=ax[i],c=cam([a[0],a[1],a[2]]);
    var ex=ox+c[0]*L*0.8, ey=oy-c[2]*L*0.8;
    ctx.strokeStyle=a[4];ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(ox,oy);ctx.lineTo(ex,ey);ctx.stroke();
    ctx.fillStyle=a[4];ctx.font='bold 11px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(a[3],ox+c[0]*L*1.05,oy-c[2]*L*1.05);
  }
}

/* ---- 交互 ---- */
var drag=null;
cv.addEventListener('mousedown',function(e){
  drag={x:e.clientX,y:e.clientY,btn:e.button,yaw:V.yaw,pitch:V.pitch,ox:V.ox,oy:V.oy,moved:0};
  cv.classList.add('drag');
});
window.addEventListener('mousemove',function(e){
  if(!drag)return;
  var dx=e.clientX-drag.x,dy=e.clientY-drag.y;
  drag.moved=Math.max(drag.moved,Math.abs(dx)+Math.abs(dy));
  if(drag.btn===2){V.ox=drag.ox+dx;V.oy=drag.oy+dy;}
  else{V.yaw=drag.yaw+dx*0.008;V.pitch=Math.max(-1.5,Math.min(1.5,drag.pitch+dy*0.008));}
  draw();
});
window.addEventListener('mouseup',function(e){
  if(drag&&drag.moved<4&&drag.btn===0)hitTest(e);
  drag=null;cv.classList.remove('drag');
});
cv.addEventListener('contextmenu',function(e){e.preventDefault();});
cv.addEventListener('wheel',function(e){
  e.preventDefault();
  var k=e.deltaY<0?1.12:1/1.12;
  var r=cv.getBoundingClientRect(),mx=e.clientX-r.left-r.width/2,my=e.clientY-r.top-r.height/2;
  V.ox=mx-(mx-V.ox)*k;V.oy=my-(my-V.oy)*k;V.scale*=k;
  draw();
},{passive:false});

// 触屏
var tch=null;
cv.addEventListener('touchstart',function(e){
  if(e.touches.length===1)tch={x:e.touches[0].clientX,y:e.touches[0].clientY,yaw:V.yaw,pitch:V.pitch};
  else if(e.touches.length===2){var a=e.touches[0],b=e.touches[1];
    tch={d:Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY),scale:V.scale};}
},{passive:true});
cv.addEventListener('touchmove',function(e){
  if(!tch)return;e.preventDefault();
  if(e.touches.length===1&&tch.yaw!==undefined){
    V.yaw=tch.yaw+(e.touches[0].clientX-tch.x)*0.008;
    V.pitch=Math.max(-1.5,Math.min(1.5,tch.pitch+(e.touches[0].clientY-tch.y)*0.008));
  }else if(e.touches.length===2&&tch.d){
    var a=e.touches[0],b=e.touches[1];
    V.scale=tch.scale*(Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY)/tch.d);
  }
  draw();
},{passive:false});

function hitTest(e){
  var r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
  var w=cv.clientWidth||1200,h=cv.clientHeight||640,best=null,bd=14;
  for(var i=0;i<GEOM.length;i++){
    var g=GEOM[i];
    var a,b;
    if(g.pt){a=proj(cam(g.p),w,h);b=a;}
    else{a=proj(cam(g.p0),w,h);b=proj(cam(g.p1),w,h);}
    var d=segDist(mx,my,a,b);
    if(d<bd){bd=d;best=g;}
  }
  if(best){SEL=best.spec;showDetail(best.spec);renderListHL();draw();}
}
function segDist(px,py,a,b){
  var dx=b[0]-a[0],dy=b[1]-a[1],L=dx*dx+dy*dy;
  var t=L?Math.max(0,Math.min(1,((px-a[0])*dx+(py-a[1])*dy)/L)):0;
  var x=a[0]+t*dx,y=a[1]+t*dy;
  return Math.hypot(px-x,py-y);
}
function renderListHL(){
  var its=document.querySelectorAll('.pitem');
  for(var i=0;i<its.length;i++)its[i].classList.toggle('on',its[i].getAttribute('data-k')===SEL);
}
function pick(k){SEL=k;showDetail(k);renderListHL();draw();}

function showDetail(k){
  var box=document.getElementById('detail');
  var s=null;for(var i=0;i<(DATA.specs||[]).length;i++)if(DATA.specs[i].key===k)s=DATA.specs[i];
  if(!s){box.innerHTML='<p style="color:#8b97a8;font-size:12.5px">'+T('d_none')+'</p>';return;}
  var p=s.product||{},r=s.result||{},idx=r.index||{};
  var els=(DATA.elements||[]).filter(function(e){return e.spec===k;});
  var e0=els[0]||{};
  var h='<div style="font-weight:700;color:#0b1f4d;font-size:14px;margin-bottom:3px">'+esc(s.label)+'</div>'+
    '<div style="margin-bottom:10px"><span class="pill '+(s.color||'na')+'">'+T('lg_'+(s.color||'na'))+
    '</span><span style="font-size:11.5px;color:#8b97a8">'+esc(s.key)+' · '+els.length+
    (LANG==='ru'?' шт.':' 件')+'</span></div>'+
    '<div class="kv">'+
    '<div class="k">'+T('type')+'</div><div class="v">'+esc(p.type||'—')+'</div>'+
    '<div class="k">DN</div><div class="v">'+esc(p.dn_mm||'—')+'</div>'+
    '<div class="k">'+T('c_mat')+'</div><div class="v">'+esc(p.material_grade_cn||'—')+' / '+esc(p.material_grade_ru||'—')+'</div>'+
    '<div class="k">'+T('storey')+'</div><div class="v">'+esc(e0.storey||'—')+'</div>'+
    '<div class="k">'+T('c_cn')+'</div><div class="v">'+verdictPill(idx.cn)+'</div>'+
    '<div class="k">'+T('c_ru')+'</div><div class="v">'+verdictPill(idx.ru)+'</div>'+
    '</div>';
  if(s.evidence&&Object.keys(s.evidence).length){
    h+='<div style="font-size:12px;color:#8b97a8;margin:4px 0 5px">'+T('c_val')+'（取自模型属性）</div><div class="tags">';
    for(var key in s.evidence)h+='<span>'+esc(key)+'='+esc(s.evidence[key])+'</span>';
    h+='</div>';
  }
  if(e0.geom&&e0.geom.k==='pt')h+='<p style="font-size:11.5px;color:#8b97a8;margin-top:8px">'+T('geom_none')+'</p>';
  if(e0.props){
    h+='<div style="font-size:12px;color:#8b97a8;margin:10px 0 5px">'+T('props')+'</div>'+
       '<table><tr><th>'+T('c_ind')+'</th><th>'+T('c_val')+'</th></tr>';
    var c=0;for(var pk in e0.props){if(c++>28)break;
      h+='<tr><td>'+esc(pk)+'</td><td>'+esc(fmtv(e0.props[pk]))+'</td></tr>';}
    h+='</table>';
  }
  box.innerHTML=h;
}

function resetView(){V.yaw=-0.62;V.pitch=0.46;fitView();draw();}
function topView(){V.yaw=0;V.pitch=1.5;fitView();draw();}
function frontView(){V.yaw=0;V.pitch=0;fitView();draw();}
function toggleLabels(){SHOW_LABELS=!SHOW_LABELS;document.getElementById('b_lbl').classList.toggle('on',SHOW_LABELS);draw();}
function toggleOnlyBad(){ONLY_BAD=!ONLY_BAD;document.getElementById('b_bad').classList.toggle('on',ONLY_BAD);draw();}

applyLang();
loadLimits();
</script>
</body>
</html>
"""
