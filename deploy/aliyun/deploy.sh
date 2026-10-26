#!/usr/bin/env bash
# 阿里云 ECS 一键部署（Ubuntu 22.04 / Debian 12；CentOS 见指南内差异）
# 用法：在服务器上 sudo bash deploy.sh 你的域名
set -euo pipefail

DOMAIN="${1:?用法: sudo bash deploy.sh <your-domain.com>}"
APP_DIR=/opt/cnru-fire-pipe
SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"   # 项目根（含 engine/ data/ examples/ scripts/）

echo "==> 1. 系统依赖"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git nginx

echo "==> 2. 拷贝项目到 $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --exclude __pycache__ --exclude '.git' "$SRC_DIR/engine" "$SRC_DIR/data" \
      "$SRC_DIR/examples" "$SRC_DIR/scripts" "$APP_DIR/"

echo "==> 3. 安装 Python 依赖"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/engine/requirements.txt" gunicorn

echo "==> 4. 写 systemd 服务"
cp "$(dirname "$0")/systemd/cnru-api.service" /etc/systemd/system/cnru-api.service
# 把服务里 python 路径指向虚拟环境 + 工作目录
sed -i "s|/usr/bin/env python3|$APP_DIR/.venv/bin/python|" /etc/systemd/system/cnru-api.service
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$APP_DIR|" /etc/systemd/system/cnru-api.service
systemctl daemon-reload
systemctl enable --now cnru-api
echo "   状态: $(systemctl is-active cnru-api)"

echo "==> 5. 写 Nginx 反代（含 HTTPS 跳转）"
cp "$(dirname "$0")/nginx/cnru-api.conf" /etc/nginx/conf.d/cnru-api.conf
sed -i "s/your-domain.com/$DOMAIN/g" /etc/nginx/conf.d/cnru-api.conf
nginx -t
systemctl reload nginx

echo "==> 6. 提示"
cat <<EOF
部署完成。请完成：
  - 在阿里云【安全组】放行 22/80/443（不要把 8000 暴露公网）。
  - 上传 SSL 证书到 /etc/nginx/ssl/$DOMAIN.pem/.key（上方 nginx 配置假定该路径）。
  - 在 server 上填入真实 .env / systemd 环境变量（LLM_API_KEY、WC_*）。
  - 小程序 app.js 的 apiBase 改为 https://$DOMAIN；并在小程序后台加入 request 合法域名（需备案）。
健康检查: curl -k https://$DOMAIN/health
EOF
