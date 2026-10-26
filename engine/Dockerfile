# 微信云托管（CloudBase Run）Dockerfile —— 免 ICP 备案，可直接跑 Python 引擎
# 构建：docker build -t cnru-fire-pipe -f engine/Dockerfile .
# 本地测试：docker run -p 8000:8000 cnru-fire-pipe
FROM python:3.11-slim

WORKDIR /app

# 先拷贝依赖声明，利用层缓存
COPY engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝引擎 + 数据（知识库 JSON）
COPY engine/ ./engine/
COPY data/ ./data/
COPY examples/ ./examples/

# 云托管健康检查路径
EXPOSE 8000
CMD ["python", "engine/api_server.py"]
