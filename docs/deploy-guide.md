# 智能旅行助手 · 阿里云轻量应用服务器部署全流程指南

> 适用环境：阿里云**轻量应用服务器** Ubuntu 24.04，2 vCPU / 2 GiB / 40 GiB ESSD，峰值带宽 200 Mbps
> 示例公网 IP：`139.196.40.216`（请替换为你自己的 IP）
> 部署方式：**Docker Compose 一键编排**（MySQL 8.0 + Redis 7 + FastAPI 后端 + Nginx 前端）
>
> 注意：轻量应用服务器**没有 ECS 的"安全组"**，端口放行功能叫**「防火墙」**（服务器详情页顶部标签）。
>
> ✅ **本机（139.196.40.216）已完成一次性系统配置**：2G Swap、Docker Engine + Compose v2、阿里云镜像加速器（`daemon.json`）。
> 在这台机器上**重新部署**时跳过第 1~4 步，直接从第 5 步开始（清理残留见第 9.3 节）。

---

## 0. 部署架构总览

```
                轻量服务器防火墙（放行 22、80）
                            │
        http://139.196.40.216:80
                            ▼
┌─────────────────────────────────────────────────────────┐
│  轻量应用服务器 Ubuntu 24.04（2C2G + 2G Swap）           │
│                                                          │
│  ┌────────────┐   /api/ 反代    ┌────────────────────┐   │
│  │ frontend   │ ──────────────▶ │ backend            │   │
│  │ nginx :80  │                 │ FastAPI :8000      │   │
│  │ Vue 静态站  │                 │ （仅绑定127.0.0.1）│   │
│  └────────────┘                 └─────────┬──────────┘   │
│                                           │              │
│                        ┌──────────────────┼──────────┐   │
│                        ▼                    ▼          ▼   │
│                 ┌────────────┐      ┌──────────┐  高德MCP │
│                 │ MySQL 8.0  │      │ Redis 7  │  LLM API │
│                 │ （数据卷）  │      │          │ （外网） │
│                 └────────────┘      └──────────┘          │
└─────────────────────────────────────────────────────────┘
```

**内存预算（2 GiB 为什么必须加 Swap）**：

| 时期 | 占用 |
|---|---|
| 运行态 | 系统 ~200 MB + MySQL ~350 MB（已优化）+ Redis ~10 MB + 后端 ~250 MB + Nginx ~10 MB ≈ **0.9 GB** |
| 构建态 | 前端 Vite 构建峰值 **0.8~1.2 GB**，与运行容器叠加会触顶 → 必须加 2 GB Swap 并串行构建 |

---

## 1. 阿里云控制台准备（约 5 分钟）

### 1.1 重置实例密码

1. 打开轻量应用服务器控制台 → 服务器列表 → 点击服务器 `Ubuntu-ctbj`
2. 点击右上角 **「更多操作」→「重置密码」**（首次也可能显示「设置密码」）
3. 设置 `root` 用户密码：**12 位以上，含大小写字母+数字+符号**，记好
4. 重置后需要 **重启** 生效（页面右上角「重启」）

### 1.2 配置防火墙（放行 HTTP 端口）

轻量服务器**没有安全组**，端口在**防火墙**中管理：

1. 服务器详情页顶部标签点击 **「防火墙」**
2. 点击 **「添加规则」**，按如下填写：

| 应用类型 | 协议 | 端口范围 | 来源 | 说明 |
|---|---|---|---|---|
| HTTP（推荐，自动带出） | TCP | `80` | `0.0.0.0/0` | 网站访问端口 |
| （无 HTTP 选项时选手动/自定义） | TCP | `80` | `0.0.0.0/0` | 同上 |

> 22 端口（SSH）轻量服务器默认已放行，在列表中确认即可。**不要添加 8000、3306、6379**——后端已绑定 `127.0.0.1`，数据库只在容器内网通信。

---

## 2. SSH 连接服务器

在本地 Windows 打开 **PowerShell** 或 Windows Terminal：

```powershell
ssh root@139.196.40.216
```

首次连接输入 `yes`，然后输入第 1.1 步设置的密码（输入时屏幕不显示，正常现象）。
看到 `root@xxx:~#` 即连接成功。**后续第 3~7 步的命令都在这个 SSH 窗口里执行。**

---

## 3. 系统初始化

### 3.1 更新系统 & 设置时区

```bash
apt update && apt upgrade -y
timedatectl set-timezone Asia/Shanghai
apt install -y git curl vim
```

### 3.2 创建 2 GB Swap（关键，勿跳过）

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10
echo 'vm.swappiness=10' >> /etc/sysctl.conf
free -h
```

`free -h` 输出中能看到 `Swap: 2.0GiB` 即成功。

---

## 4. 安装 Docker Engine + Compose

> 国内服务器访问 `get.docker.com` 经常报 `Connection reset by peer`，**直接用 apt 安装 Ubuntu 官方源的 Docker** 最稳（阿里云默认 apt 源走内网镜像）：

```bash
apt update
apt install -y docker.io docker-compose-v2
systemctl enable --now docker
docker --version && docker compose version
```

两条 version 命令都能输出版本号即安装成功（docker.io 版本比官方略旧，部署完全够用；`docker-compose-v2` 提供 `docker compose` 子命令）。

### 4.1 配置镜像加速（必须，否则拉不动 Docker Hub 镜像）

国内服务器直连 Docker Hub 拉取 MySQL / Nginx 等镜像会超时或连接重置，必须配加速器：

1. 登录阿里云控制台 → 搜索 **容器镜像服务 ACR** → 左下角 **镜像工具 → 镜像加速器**
2. 复制你的专属加速器地址（形如 `https://xxxx.mirror.aliyuncs.com`），然后执行：

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://arfyagjp.mirror.aliyuncs.com"]
}
EOF
systemctl daemon-reload && systemctl restart docker
```

> 把 `xxxx.mirror.aliyuncs.com` 替换成控制台里你的专属地址。
> 验证：`docker pull hello-world && docker run --rm hello-world`，看到 "Hello from Docker!" 即正常。

---

## 5. 获取项目代码（二选一）

### 方式 A：服务器上 Git Clone（推荐）

```bash
cd /opt
git clone https://github.com/xiaoqingxu271/smart-trip-planner.git
cd smart-trip-planner
```

> 若 GitHub 速度过慢或超时，使用代理镜像：
> `git clone https://ghfast.top/https://github.com/xiaoqingxu271/smart-trip-planner.git`

### 方式 B：本地打包通过 SCP 上传

在**本地 PowerShell**（不是 SSH 窗口）执行，先打包（排除无用大目录），再上传：

```powershell
cd "D:\Code\Practical Project"
# 打包（需要本机有 tar，Windows 10/11 自带）
tar -cf trip.tar --exclude=smart-trip-planner/frontend/node_modules `
  --exclude=smart-trip-planner/frontend/dist `
  --exclude=smart-trip-planner/.git `
  --exclude=smart-trip-planner/backend/.pytest_cache `
  smart-trip-planner
# 上传
scp trip.tar root@139.196.40.216:/opt/
```

回到 **SSH 窗口**解压：

```bash
mkdir -p /opt && cd /opt
tar -xf trip.tar && cd smart-trip-planner
```

---

## 6. 配置密钥与环境变量

### 6.1 配置 Compose 变量（MySQL 密码）

```bash
cd /opt/smart-trip-planner
cp .env.example .env
vim .env
```

按需修改 `MYSQL_ROOT_PASSWORD`（也可保持默认）。该密码会**同时**注入 MySQL 容器和后端容器，无需再手动同步。

### 6.2 配置后端密钥（backend/.env）

`backend/.env` 含密钥、不随 Git 提交，需要单独创建。**最简单的方式：从本地 SCP 上传。**

在**本地 PowerShell** 执行：

```powershell
scp "D:\Code\Practical Project\smart-trip-planner\backend\.env" root@139.196.40.216:/opt/smart-trip-planner/backend/.env
```

> 备选方案：在服务器上手动创建 `vim /opt/smart-trip-planner/backend/.env`，内容照抄本地该文件。
>
> 说明：`backend/.env` 里的 `MYSQL_HOST / MYSQL_PASSWORD / REDIS_URL` 写什么都没关系，`docker-compose.yml` 已强制用容器服务名和统一密码覆盖它们。

### 6.3（建议）开启访问鉴权

服务直接暴露在公网，建议在 `backend/.env` 末尾增加以下任一项：

```bash
# 方案1：单访问码（最简单，所有人凭一个密码进入）
APP_PASSWORD=你的强密码

# 方案2：多用户注册登录（行程按用户隔离，与 APP_PASSWORD 二选一）
AUTH_MODE=user
```

修改后不重启不生效（第 7 步首次启动会直接读取）。

---

## 7. 构建并启动（串行构建，防止内存不足）

### 7.1 国内网络适配：镜像引用改用 DaoCloud 完整域名（重要）

即使配了镜像加速器，BuildKit 构建时仍可能因向 `docker.io` 请求 metadata 被干扰而报
`... not found`（典型现象：`docker pull alpine` 成功，但 `python:3.12-slim` 解析失败）。
彻底解决办法是在**服务器上**把镜像引用直接改成 DaoCloud 完整域名（仓库源码保持标准写法，
不影响 CI 和其他环境）：

```bash
cd /opt/smart-trip-planner

# 验证 DaoCloud 通路
docker pull docker.m.daocloud.io/library/python:3.12-slim

# 后端基础镜像
sed -i 's|^FROM python:3.12-slim|FROM docker.m.daocloud.io/library/python:3.12-slim|' backend/Dockerfile

# 前端基础镜像（node 构建阶段 + nginx 运行阶段）
sed -i 's|^FROM node:22-alpine|FROM docker.m.daocloud.io/library/node:22-alpine|; s|^FROM nginx:alpine|FROM docker.m.daocloud.io/library/nginx:alpine|' frontend/Dockerfile

# compose 中的 MySQL、Redis 镜像
sed -i 's|image: mysql:8.0|image: docker.m.daocloud.io/library/mysql:8.0|; s|image: redis:7-alpine|image: docker.m.daocloud.io/library/redis:7-alpine|' docker-compose.yml

# 确认 5 处引用全部带 docker.m.daocloud.io/library/ 前缀
grep -Hn -e "FROM" -e "image:" backend/Dockerfile frontend/Dockerfile docker-compose.yml
```

> DaoCloud 拉不通时，把上述命令中 `docker.m.daocloud.io` 统一替换为 `docker.1panel.live`。

### 7.2 串行构建与启动

> 依赖下载已在 Dockerfile 内置国内源加速（后端 apt/pypi 走阿里云、前端 npm 走 npmmirror），
> 7.1 只需处理 5 处**基础镜像**引用。2026-10-09 实测：未加依赖源时构建耗时 28 分钟
> （pypi/npm 官方源被限速），内置后预期 10~15 分钟。

2 GiB 机器**不要**直接 `up --build`（前后端并行构建会 OOM），分两步：

```bash
# 1) 串行构建镜像（首次约 10~15 分钟）
docker compose build backend
docker compose build frontend

# 2) 启动全部服务
docker compose up -d
```

观察启动过程：

```bash
docker compose ps        # 四个服务状态应为 running / healthy
docker compose logs -f backend   # 看后端日志，Ctrl+C 退出查看（不会停止服务）
```

后端首次启动会自动建库建表（执行 `sql/init.sql`）；首次规划行程时 `npx` 会下载高德 MCP 服务器包，可能多等 1~2 分钟。

---

## 8. 验证部署

```bash
# 服务器本机验证
curl http://127.0.0.1/api/health
```

正常返回（2026-10-09 实测样例）：

```json
{"status":"ok","demo_mode":false,"auth_required":false,"auth_mode":"none","mysql":true,"redis":true,"message":""}
```

然后在**本地浏览器**访问：

- 应用首页：**http://139.196.40.216**
- 建议真实发起一次行程规划，确认 LLM、高德 MCP、地图、数据库全链路正常
  （2026-10-09 实测：真实模式 POST /api/trip/plan 72 秒返回完整行程并自动入库，
  `/api/trip/history` 可查到该行程）

> 后端 API 文档（/docs）未通过 Nginx 暴露。需要查看时，在本地 PowerShell 建立 SSH 隧道：
> `ssh -L 8000:127.0.0.1:8000 root@139.196.40.216`
> 然后浏览器访问 http://localhost:8000/docs

---

## 9. 日常运维速查

所有命令均在 `/opt/smart-trip-planner` 目录下执行。

| 操作 | 命令 |
|---|---|
| 查看服务状态 | `docker compose ps` |
| 查看实时日志 | `docker compose logs -f` 或 `docker compose logs -f backend` |
| 重启某个服务 | `docker compose restart backend` |
| 停止全部 | `docker compose down`（数据卷保留，数据不丢） |
| 更新代码后重新部署 | `git pull && docker compose build backend frontend && docker compose up -d` |
| 清理无用镜像释放磁盘 | `docker image prune -f` |
| 查看磁盘/内存 | `df -h` / `free -h` |

### 9.1 MySQL 数据备份与恢复

```bash
# 备份（执行后输入 .env 中设置的 MySQL 密码）
docker compose exec mysql mysqldump -uroot -p trip_planner > /opt/trip_backup_$(date +%F).sql

# 恢复
cat /opt/trip_backup_xxxx-xx-xx.sql | docker compose exec -T mysql -uroot -p trip_planner
```

> 数据库文件存储在 Docker 数据卷 `mysql_data` 中，`docker compose down` 不会删除；只有 `docker compose down -v` 会删卷，**日常严禁加 `-v`**。

### 9.2 开机自启

Docker 已设开机自启，且 compose 中所有服务配置了 `restart: unless-stopped`，服务器重启后会自动拉起，无需额外操作。

### 9.3 重新部署（清理残留 → 全新拉起）

需要彻底重置（旧构建产物污染、compose 配置大改、清空数据重来）时按此流程执行，
已在 139.196.40.216 实机验证（2026-10-09）：

```bash
cd /opt/smart-trip-planner

# 1) 停止并删除容器、网络、数据卷、项目镜像（⚠️ -v 会清空 MySQL 数据）
docker compose down -v --rmi all --remove-orphans

# 2) 兜底清理游离卷/悬空镜像（先 docker volume ls 确认没有要保留的卷）
docker volume prune -af
docker image prune -af

# 3) 删除旧代码目录（其中未提交的本地改动会丢失，确认无价值后执行）
cd /opt && rm -rf smart-trip-planner

# 4) 拉最新代码（GitHub 慢就在 URL 前加 https://ghfast.top/ 前缀）
cd /opt && git clone https://github.com/xiaoqingxu271/smart-trip-planner.git
cd smart-trip-planner

# 5) 重建两份配置：cp .env.example .env（root 密码）；backend/.env 从本地 SCP 重新上传
# 6) 按第 7 步重新构建启动：sed 换 DaoCloud 源 → 串行 build backend/frontend → up -d
```

> 清理后需要重做的只有**两份 `.env`**（根目录 `.env` + `backend/.env`）。
> Swap（3.2）、Docker 与加速器（第 4 步）是系统级一次性配置，不随项目目录删除，无需重做。

---

## 10. 安全清单

- [x] 后端端口仅绑定 `127.0.0.1`，防火墙只放行 22 / 80 / 443
- [ ] 已配置 `APP_PASSWORD` 或 `AUTH_MODE=user`
- [ ] 高德控制台为 **JS API Key 配置域名白名单**（填 `139.196.40.216` 或后续域名），防止 Key 被盗刷
- [x] `.env`、`backend/.env` 权限：`chmod 600 .env backend/.env`
- [x] HTTPS 已上线：Cloudflare Origin CA 证书 + nginx 443（见第 12 节）

---

## 11. 常见问题

**Q1：构建前端时报 `JavaScript heap out of memory` / 进程被 Killed**
Swap 没生效或构建时内存仍不足。先 `free -h` 确认 Swap 存在；仍失败可给 Node 加大堆上限后单独重建：
`docker compose build --build-arg NODE_OPTIONS=--max-old-space-size=1536 frontend`（需 Dockerfile 支持时），或临时停掉 MySQL 再构建：`docker compose stop mysql && docker compose build frontend && docker compose up -d`。

**Q2：浏览器访问显示 502 Bad Gateway**
后端还没启动完或启动失败。`docker compose logs backend` 查看；常见为 MySQL 健康检查未通过时后端在等待，等 1~2 分钟即可。

**Q3：浏览器打不开页面（连接超时）**
防火墙没放行 80 端口，回到第 1.2 步检查。

**Q4：后端日志报 MySQL Access denied**
说明改过 `.env` 里的 `MYSQL_ROOT_PASSWORD` 但数据卷是用旧密码初始化的。若为全新部署无数据需保留，可 `docker compose down -v`（会清空数据）后重新 `up -d`；有数据则用旧密码或手动改密。

**Q5：git clone / 拉取镜像很慢**
GitHub 用第 5 步的代理镜像；Docker 镜像检查第 4.1 步加速器是否生效（`docker info | grep -A2 "Registry Mirrors"`）。

**Q6：行程规划一直转圈或超时**
首次调用需 npx 下载 MCP 包 + 4 个 Agent 串行执行，真实模式约 30~90 秒；若服务器访问外网 API 不稳定，检查 `curl https://api.deepseek.com` 是否通。

**Q7：Windows Git Bash 用 curl 测 /api/trip/plan 报 "There was an error parsing the body"**
Git Bash 控制台把中文按 GBK 编码发出，FastAPI 按 UTF-8 解析失败。把请求体写成 UTF-8 文件再发：

```bash
python -c "import json,pathlib; pathlib.Path('req.json').write_bytes(json.dumps({'destination':'杭州','days':1}, ensure_ascii=False).encode('utf-8'))"
curl -s -X POST http://139.196.40.216/api/trip/plan -H "Content-Type: application/json; charset=utf-8" --data-binary @req.json
```

---

## 12. HTTPS：Cloudflare Origin CA 证书（域名在 Cloudflare 托管）

架构：浏览器 ──HTTPS──▶ Cloudflare 边缘（有效期为 15 年的 Origin 证书仅被 CF 信任）
──HTTPS 回源 443──▶ 服务器 nginx。CF 控制台 SSL/TLS 模式建议 **Full (strict)**。

### 12.1 前置

1. Cloudflare DNS：`smartrip.dpdns.org` A 记录指向 `139.196.40.216`，代理状态**橙色云**（Proxied）。
2. 阿里云轻量防火墙放行 `TCP 443`（与 80 同法添加规则）。

### 12.2 签发 Origin 证书

CF 控制台 → SSL/TLS → **Origin Server** → Create Certificate → 默认（RSA 2048，
主机名 `*.smartrip.dpdns.org, smartrip.dpdns.org`，15 年）→ 复制两段内容分别保存为
`fullchain.pem`（Origin Certificate）与 `privkey.pem`（Private Key）。

### 12.3 服务器放置证书（私钥绝不进 Git）

仓库已配置好：`nginx.conf` 监听 443、compose 挂载 `./frontend/certs` → `/etc/nginx/certs:ro`
（`frontend/certs/` 已在 `.gitignore`）。只需在服务器放置文件：

```bash
mkdir -p /opt/smart-trip-planner/frontend/certs && chmod 700 $_
# 本地 PowerShell 上传（或任意 sftp 工具）：
# scp fullchain.pem privkey.pem root@139.196.40.216:/opt/smart-trip-planner/frontend/certs/
chmod 600 /opt/smart-trip-planner/frontend/certs/*
```

> **80 与 443 同时服务、不做强制跳转**：若 CF SSL 模式是 Flexible，CF 回源走 80，
> 源站强制跳 443 会造成浏览器循环重定向；Full/Full(strict) 下 CF 直接走 443，不受影响。

### 12.4 生效与验证

```bash
cd /opt/smart-trip-planner
docker compose build frontend && docker compose up -d

# 服务器本机（Origin 证书不被系统信任，-k 跳过校验属预期）
curl -sk https://127.0.0.1/api/health

# 本地带 SNI 直连源站（-k：Origin 证书仅被 CF 信任，本地系统校验不过属预期；-v 可看证书域名）
curl -k --resolve smartrip.dpdns.org:443:139.196.40.216 https://smartrip.dpdns.org/api/health

# 经 Cloudflare 的正式链路
curl https://smartrip.dpdns.org/api/health
```

浏览器直接访问 `https://smartrip.dpdns.org` 即可。证书 2041 年到期，期间无需续期；
换域名需重签发并替换 `frontend/certs/` 下两个文件后 `docker compose restart frontend`。
