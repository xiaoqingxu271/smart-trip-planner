# 智能旅行助手后端：Python + Node（高德 MCP 服务器以 npx 子进程运行，容器内需要 Node）
# 使用 3.12-slim：生态兼容性最好；勿用 3.14（标签新，部分依赖无预编译 wheel）
FROM python:3.12-slim

# apt 源切阿里云镜像：官方 deb.debian.org 从国内云主机晚间常被限速到 KB/s（trixie 的源文件是 debian.sources）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*

# 从 npmmirror 安装 Node.js 22（比 nodesource 稳定，npmmirror 全球可访问）
RUN curl -fsSL https://npmmirror.com/mirrors/node/v22.23.3/node-v22.23.3-linux-x64.tar.xz \
    | tar -xJ --strip-components=1 -C /usr/local \
    && node --version && npm --version \
    && npm config set registry https://registry.npmmirror.com

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY sql ./sql

# .env 不打进镜像（密钥），由 compose 的 env_file 注入环境变量
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
