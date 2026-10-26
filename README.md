# 中俄消防金属管材合规 Agent（CN-RU Fire Pipe Compliance Agent）

> 输入管材参数、自然语言问题或 IFC 模型 → 自动输出【中国法规判定】+【俄罗斯法规判定】+【EAC 认证路径】，
> 每条结论都带标准条文引用。

**在线演示：** `http://47.94.235.211` — 三个入口，界面可切换 RU / 中文。

面向出口俄罗斯／欧亚经济联盟的消防金属管材企业，回答三个问题：我的管子在中国的规范下合规吗？在俄罗斯的规范下合规吗？要不要做 EAC 认证？

---

## 一、技术路线

1. **知识库**：中俄法规条款数字化为机读 JSON，共 23 份中俄文件 + 2 部欧亚经济联盟技术法规 → **181 项受控特征**，每项要求归入 **10 种要求形式**之一。
2. **规则引擎**：判定由确定性规则完成（通过／不通过／数据不足），不做"估计"。数据覆盖率低于 50 % 时不给出类别，只报"数据不足"。
3. **LLM 表达层**：语言模型只负责把结构化判定写成通顺的报告，不参与判定，因此结果可复现、可追溯。
4. **三个入口**：表单参数、自然语言对话、IFC 模型解析——共用同一套判定内核。

## 二、法规覆盖范围

**中国（9 项）**：GB/T 17395-2024（钢管尺寸偏差）、GB/T 8163-2018（输送流体用无缝钢管）、GB/T 3091-2015（低压流体输送用焊接钢管）、GB/T 5135.20-2010（自动喷水灭火系统·涂覆钢管）、GB/T 14976-2012（不锈钢无缝管）、GB/T 12771-2019（不锈钢焊接管）、GB/T 5223-2025（预应力钢丝※）、GB/T 1499.2-2024（热轧带肋钢筋※）、GB/T 13793-2016（直缝电焊钢管）。

**俄罗斯（14 项标准 + 2 部技术法规）**
- 技术法规：**ТР ТС 032/2013**（承压设备安全，含 P×DN 类别判定表）、**ТР ЕАЭС 043/2017**（消防安全与灭火器材）。
- 产品与系统标准：ГОСТ 3262-75、ГОСТ 8731-87 / 8732-78 / 8734-75、ГОСТ 10704-91 / 10705-80 / 10706-76、ГОСТ 20295-85、ГОСТ 9940-81、ГОСТ 11068-81、СП 10.13130.2020 / СП 485.1311500.2020、ГОСТ 5781-82（※）、ГОСТ 6727-80（※）、ГОСТ ISO/IEC 17025-2019（管理性）。

※ 混凝土用钢标准，仅当产品含钢筋／钢丝附件时启用。

**核心判定逻辑（按官方文本核验，ред. 24.11.2023）**
- 032 与 043 依据的原理不同：032 看超压风险（P / DN / 介质 → P×DN 判类别），043 看用途与材料。
- 消防水管（水＝Группа 2，DN ≤ 200）通常**不属** 032 范围（须 P > 1 МПа 且 DN > 200 且 P×DN > 500 МПа·мм，ст.2 п.2 и）；但消防系统管道**必须**按 043 强制认证。
- 消防专用标准与基管标准配套判定：中 GB/T 5135.20-2010，俄 СП 10.13130.2020 / СП 485.1311500.2020。

## 三、目录结构

```
├── engine/                 判定与接口层
│   ├── api_server.py       FastAPI：/api/evaluate、/api/report、/api/chat、/api/analyze
│   ├── evaluate.py         规则引擎（判定 + 032 类别 + 043 适用 + 指数化）
│   ├── llm_report.py       报告生成（mock / OpenAI 兼容）
│   ├── report_ru.py        俄文报告渲染层
│   ├── rag_chat.py         对话入口（BM25 检索 + 判定）
│   ├── ifc_pipe.py         IFC 模型解析与三维标注
│   ├── model_page.py       模型分析页面
│   ├── pipeline.py         端到端：评估 → 报告 →（可选）微信推送
│   ├── push_wechat.py      企业微信消息推送（dry-run / real）
│   ├── agent_prompt.md     LLM 表达层提示词模板
│   ├── requirements.txt    依赖
│   └── test_smoke.py       032 类别判定冒烟测试
├── data/                   机读知识库
│   ├── cn_standards_final.json      中国标准（9 项）
│   ├── ru_standards.json            俄罗斯标准 + 技术法规（14 + 2）
│   ├── cn_indicator_ru.json         中国指标俄文名（99 项）
│   ├── cn_stainless_standards.json  不锈钢源文件
│   ├── ru_stainless_standards.json  不锈钢源文件
│   └── faq.json                     常见问题
├── scripts/                知识库校验、合并、俄语补丁
├── examples/               示例输入与测试用 IFC 模型
├── deploy/aliyun/          部署：systemd、nginx、deploy.sh、.env.example
└── docs/                   文档
    ├── 产品介绍_中俄消防管材合规Agent.pdf     产品介绍（PDF）
    ├── 使用说明_中俄消防管材合规Agent.pdf     使用说明（PDF）
    ├── 模型与配置_Модели_и_конфигурация.md   模型分层、参数与切换方式
    ├── PRODUCT_GUIDE.md                       产品说明（Markdown）
    └── 案例归档/                              示例分析结果与测试模型
```

## 四、快速开始（本地）

```bash
git clone https://github.com/<你的用户名>/cnru-fire-pipe.git
cd cnru-fire-pipe

python -m venv .venv
# Windows: .venv\Scripts\activate ；Linux/macOS: . .venv/bin/activate
pip install -r engine/requirements.txt

# 外部模型密钥（不要提交到仓库）
cp deploy/aliyun/.env.example .env      # 然后填写 LLM_API_KEY 等

uvicorn engine.api_server:app --reload --port 8000
```

打开 `http://127.0.0.1:8000`（对话）· `/form`（表单）· `/model`（IFC 模型分析）。

也可以直接跑命令行：

```bash
python engine/evaluate.py examples/sample_input_firepipe.json          # 逐条判定
python engine/llm_report.py examples/sample_input_firepipe.json        # mock 报告
python engine/pipeline.py examples/sample_input_firepipe.json --push --userid zhangsan
```

## 五、部署（阿里云示例）

```bash
cp -r deploy/aliyun/systemd/cnru-api.service /etc/systemd/system/
cp deploy/aliyun/nginx/cnru-api.conf /etc/nginx/conf.d/
mkdir -p /etc/cnru-fire-pipe && cp .env.example /etc/cnru-fire-pipe/env   # 填真实密钥
systemctl daemon-reload && systemctl enable --now cnru-api
```

`EnvironmentFile=/etc/cnru-fire-pipe/env` 保存密钥，仓库里只有 `.env.example`。

## 六、输出示例

- 中国侧：GB/T 3091-2015 镀锌焊接钢管逐项判定（通过／不通过／数据不足 + 条款号）
- 俄罗斯侧：ГОСТ 3262-75 逐项判定（含水压、压扁、镀锌层）
- ТР ТС 032/2013：DN50 / 1,6 МПа / 水 → 不在适用范围（ст.2 п.2 и）
- ТР ЕАЭС 043/2017：消防用途 → 强制认证
- 指数化：中国 CI / 俄罗斯 CI + 评级（A / B / C / D；覆盖率 < 50 % 记 NA）

## 七、数据与保密

- 仓库内**不含**任何真实密钥、服务器环境文件、虚拟环境与备份包；`.env`、`*.pem`、`*.key` 已被 `.gitignore` 排除。
- 两个 9 MB 的真实工程 IFC 模型未入库（`.gitignore`），需要时用 Git LFS 或放在 `docs/` 外部。
- 贸易数据来源：UN Comtrade（ТН ВЭД 7304 / 7306 / 7307）。

## 八、模型与配置

哪一层是"模型"、参数在哪里、如何切换，见 [`docs/模型与配置_Модели_и_конфигурация.md`](docs/模型与配置_Модели_и_конфигурация.md)。要点：

| 层 | 实现 | 是否调用大模型 |
|---|---|---|
| 知识库（181 特征 / 10 种要求形式） | `data/*.json` | 否 |
| 判定（规则引擎） | `engine/evaluate.py` | 否 |
| 检索（对话入口） | `engine/rag_chat.py`，BM25 `k1=1,5` `b=0,75` + 重排 | 否 |
| 表达（报告措辞） | `engine/llm_report.py`，OpenAI 兼容接口，默认 `deepseek-chat`，`temperature=0,2` | 是（可关闭） |

- 仓库**不含本地模型权重**，不需要 GPU；`mock` 模式完全离线。
- 演示站当前运行在 `LLM_PROVIDER=mock`：判定与检索照常工作，报告由模板生成。
- 切到真实模型只改环境变量：`LLM_PROVIDER=openai`、`LLM_API_KEY`、`LLM_MODEL`。

## 九、免责声明

本系统输出为**合规初筛参考**，所有结论须由认证机构／认可实验室（ГОСТ ISO/IEC 17025-2019）最终确认，不构成法律意见或认证承诺。

## 十、作者与许可

Ши Юэ（Shi Yue），2026。

许可证未选定：如需开源使用，建议补一份 `LICENSE`（例如 MIT）；否则视为保留所有权利。
