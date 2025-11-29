基于GCP实例的动态 IP 代理池控制系统，主要是5美元赠金薅多了利用下。

## 架构

- **控制端 (Master)**：Flask Web 服务，提供面板和 API
- **打工端 (Worker)**：部署在 GCP 小鸡上，提供 SOCKS5/HTTP 代理并接受换 IP 指令

## 端口说明

| 端口 | 用途 |
|------|------|
| 5000 | 控制端 Web 服务 |
| 10086 | Worker SOCKS5 代理 |
| 10010 | Worker HTTP 代理 |
| 4444 | Worker 控制接口（写死了，用于gcp小鸡接收换 IP 指令） |

## 控制端部署

### 方式一：Docker Compose（推荐）

```bash
# 克隆项目
git clone https://github.com/kbtit25/rotating-gcp-proxy.git
cd rotating-gcp-proxy

# 创建配置文件
cp .env.example .env
nano .env  # 修改 API_TOKEN

# 创建数据目录
mkdir -p data

# 启动
docker-compose up -d
```

### 方式二：Docker 直接运行

```bash
docker run -d \
  --name rotating-gcp-proxy \
  -p 5000:5000 \
  -e API_TOKEN=your_token_here \
  -e ADMIN_PATH=/secret_panel \
  -e TIMEZONE=8 \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  kbtit/rotating-gcp-proxy:latest
```

### 方式三：Python 虚拟环境

```bash
# 安装依赖
apt update && apt install -y python3 python3-pip python3-venv

# 克隆项目
git clone https://github.com/kbtit25/rotating-gcp-proxy.git
cd rotating-gcp-proxy

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配置环境变量
export API_TOKEN=your_token_here
export ADMIN_PATH=/secret_panel
export TIMEZONE=8
export DATA_DIR=./data

# 创建数据目录
mkdir -p data

# 运行
python app.py
```

## GCP Worker 部署

### 前置要求

1. 创建 GCP 实例时选择 **"允许对所有 Cloud API 的完整访问权限"**，GCP小鸡所在项目的服务账号至少要有Compute Network Admin和Compute Instance Admin v1权限，没自己删改过ima的普通账号是自带这两权限的不用特意设置。GCP面板进行小鸡实例建立/启动时要勾选拥有所有cloud api权限，这个选项下面就有开机自启动脚本设置处。
2. 配置防火墙规则，开放端口：`10086`, `10010`, `4444`

### 开机自启脚本

在 GCP 创建实例时，将以下脚本填入 **启动脚本 (Startup Script)**：

```bash
#! /bin/bash

# ==================== 配置区 (请修改) ====================
# Master 控制端地址 (不要带 http://)
MASTER_IP="控制端ip"
MASTER_PORT=控制端端口
TOKEN="鉴权"

# 代理端口
SOCKS_PORT=10086
HTTP_PORT=10010
CONTROL_PORT=4444(由于app.py写死的，换这个得改下app.py)

# 用户名密码 (留空则每次启动随机生成，建议留空)
PROXY_USER=""
PROXY_PASS=""
# ========================================================

# 0. 确保只运行一次安装逻辑 (避免重启重复安装消耗时间)
if [ ! -f /var/log/proxy_setup_done ]; then
    echo "首次启动，安装依赖..."
    apt-get update
    apt-get install -y wget curl python3
    
    # 安装 Gost
    wget -qO - "https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz" | gunzip > /usr/local/bin/gost
    chmod +x /usr/local/bin/gost
    
    touch /var/log/proxy_setup_done
fi

# 1. 杀掉可能残留的旧进程
pkill gost
pkill -f worker.py

# 2. 生成随机账号 (如果是重启，重新生成更安全)
if [ -z "$PROXY_USER" ]; then
    PROXY_USER=$(tr -dc a-z0-9 </dev/urandom | head -c 11)
fi
if [ -z "$PROXY_PASS" ]; then
    PROXY_PASS=$(tr -dc a-z0-9 </dev/urandom | head -c 15)
fi

# 3. 获取元数据
ZONE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/zone" | awk -F/ '{print $NF}')
REGION=$(echo $ZONE | cut -d- -f1-2)
INSTANCE_NAME=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name")
INSTANCE_ID=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/id")

# 4. 自动开启 Private Google Access (幂等操作，跑多次没关系)
gcloud compute networks subnets update default --region=$REGION --enable-private-ip-google-access || true

# 5. 启动 Gost
nohup /usr/local/bin/gost -L "${PROXY_USER}:${PROXY_PASS}@:${SOCKS_PORT}?socks" -L "${PROXY_USER}:${PROXY_PASS}@:${HTTP_PORT}?http" > /var/log/gost.log 2>&1 &

# 6. 写入 Worker 控制脚本 (始终覆盖，确保代码最新)
cat <<PYEOF > /usr/local/bin/worker.py
import http.server
import subprocess
import json
import urllib.parse
import urllib.request

MASTER_URL = "http://${MASTER_IP}:${MASTER_PORT}/api/report"
TOKEN = "${TOKEN}"
SOCKS_PORT = ${SOCKS_PORT}
HTTP_PORT = ${HTTP_PORT}
CONTROL_PORT = ${CONTROL_PORT}
MY_USER = "${PROXY_USER}"
MY_PASS = "${PROXY_PASS}"
INSTANCE_ID = "${INSTANCE_ID}"
INSTANCE_NAME = "${INSTANCE_NAME}"
ZONE = "${ZONE}"
REGION = "${REGION}"

def get_public_ip():
    try:
        return urllib.request.urlopen("http://checkip.amazonaws.com", timeout=5).read().decode().strip()
    except:
        return "0.0.0.0"

def get_current_tier():
    try:
        result = subprocess.check_output(
            f"gcloud compute instances describe {INSTANCE_NAME} --zone={ZONE} --format='get(networkInterfaces[0].accessConfigs[0].networkTier)'",
            shell=True, timeout=30, stderr=subprocess.DEVNULL
        ).decode().strip()
        return result.upper() if result else "UNKNOWN"
    except:
        return "UNKNOWN"

def report_to_master(status="online"):
    ip = get_public_ip()
    tier = get_current_tier()
    data = {
        "id": INSTANCE_ID,
        "ip": ip,
        "socks_port": SOCKS_PORT,
        "http_port": HTTP_PORT,
        "user": MY_USER,
        "pass": MY_PASS,
        "region": REGION,
        "tier": tier,
        "status": status
    }
    try:
        req = urllib.request.Request(MASTER_URL, data=json.dumps(data).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": TOKEN
        })
        urllib.request.urlopen(req, timeout=10)
    except:
        pass

report_to_master("online")

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if params.get("key", [""])[0] != TOKEN:
            self.send_response(403); self.end_headers()
            return
        
        tier = params.get("tier", ["standard"])[0].lower()
        
        if parsed.path == "/refresh":
            current_tier = get_current_tier()
            if tier == "toggle": new_tier = "STANDARD" if current_tier == "PREMIUM" else "PREMIUM"
            else: new_tier = tier.upper()
            
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"status": "refreshing", "from": current_tier, "to": new_tier}).encode())
            
            report_to_master("changing")
            
            cmd = f'''
gcloud compute instances delete-access-config {INSTANCE_NAME} --zone={ZONE} --access-config-name="External NAT"
sleep 5
gcloud compute instances add-access-config {INSTANCE_NAME} --zone={ZONE} --access-config-name="External NAT" --network-tier={new_tier}
sleep 10
python3 /usr/local/bin/worker.py report_only
'''
            subprocess.Popen(f"nohup sh -c '{cmd}' > /tmp/refresh.log 2>&1 &", shell=True)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report_only":
        report_to_master("online"); sys.exit(0)
    server = http.server.HTTPServer(('0.0.0.0', CONTROL_PORT), Handler)
    server.serve_forever()
PYEOF

# 7. 启动 Worker
nohup python3 /usr/local/bin/worker.py > /var/log/worker.log 2>&1 &
```

或者 SSH 进入实例后手动执行。

### 脚本配置项

| 变量 | 说明 |
|------|------|
| MASTER_IP | 控制端 IP（必填） |
| MASTER_PORT | 控制端端口，默认 5000 |
| TOKEN | 与控制端相同的 API_TOKEN（必填） |
| SOCKS_PORT | SOCKS5 端口，默认 10086 |
| HTTP_PORT | HTTP 代理端口，默认 10010 |
| CONTROL_PORT | 控制接口端口，固定 4444 |
| PROXY_USER | 代理用户名，留空随机生成 |
| PROXY_PASS | 代理密码，留空随机生成 |

## API 文档

访问 `/api/docs?key=<TOKEN>` 查看完整 API 文档。

### 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/list | 获取所有代理列表 |
| POST | /api/refresh | 触发换 IP |
| GET | /api/events | 获取事件日志 |
| POST | /api/config | 修改配置 |

### 换 IP 示例

```bash
# 单个节点
curl -X POST -H "Authorization: TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "实例ID", "tier": "standard"}' \
  http://控制端IP:5000/api/refresh

# 批量节点
curl -X POST -H "Authorization: TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["ID1", "ID2"], "tier": "premium"}' \
  http://控制端IP:5000/api/refresh

# 全部节点
curl -X POST -H "Authorization: TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"all": true, "tier": "toggle"}' \
  http://控制端IP:5000/api/refresh
```

### tier 参数

| 值 | 说明 |
|------|------|
| standard | 标准层级（200GB 免费流量） |
| premium | 高级层级（更快，但流量收费） |
| toggle | 自动切换（当前是标准就换高级，反之亦然） |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| API_TOKEN | 访问密钥 | 无（必填） |
| ADMIN_PATH | 面板入口路径 | /secret_panel |
| TIMEZONE | 时区 | 8 |
| DATA_DIR | 数据目录 | . |

## 注意事项

1. GCP 标准层级每月有 200GB 免费出站流量
2. 换 IP 时会短暂断网（约 一两分钟）
3. Worker 端口 4444 固定，用于接收控制端指令
4. 可能有被谷歌风控风险，虽然我觉得不至于
