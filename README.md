# MicroSandbox Admin UI

[MicroSandbox](https://microsandbox.dev/) 轻量级沙箱容器运行时的 Web 管理后台。基于 **FastAPI + Jinja2 + HTMX + xterm.js** 构建，通过官方 `microsandbox` Python SDK 提供沙箱、镜像、快照、卷等资源的实时管理能力。

- 项目主页：https://microsandbox.dev/
- 文档：https://docs.microsandbox.dev/
- GitHub：https://github.com/topabomb/msb-admin

## 功能

### 沙箱生命周期管理
- 创建、启动、停止、强杀、删除沙箱
- 批量操作：通过逗号分隔名称批量启动/停止/强杀/删除
- 分页列表 + 搜索过滤（`?offset=&limit=&search=`)
- 实时日志（WebSocket）
- 实时指标：CPU、内存（WebSocket）
- 集群级指标：所有沙箱的 CPU%、内存、磁盘 R/W、网络 R/W、运行时长（WebSocket 自动刷新）
- 交互式网页终端（xterm.js + WebSocket）
- 执行任意命令并查看结果
- SSH 远程执行 + SFTP 文件读写/创建/删除
- 沙箱排水（`request_drain`），安全下线

### 镜像管理
- 查看所有可用镜像及元数据
- 拉取镜像（通过临时沙箱，完成后自动清理）
- 清理未使用的镜像层，回收磁盘空间

### 快照管理
- 查看、创建、删除快照（支持 labels 和 record_integrity）
- 导出快照为可下载文件（临时文件自动清理）
- 从快照恢复沙箱
- 导入快照从文件
- 重建快照索引
- 验证快照完整性

### 卷管理
- 查看卷列表及元数据
- 浏览卷文件系统（树形目录 + 文件内容）
- 文件操作：读、写、创建、重命名、删除、上传
- 目录操作：创建、重命名

### 健康与诊断
- SDK 连通性健康检查接口
- 完善的错误报告，包含 HTTP 状态码（400/404/409/504）

## 快速开始

### 前置条件

- Python 3.11+
- 已安装并配置 MicroSandbox SDK
- 可访问 MicroSandbox 运行时

### 安装

```bash
# 克隆仓库
git clone https://github.com/topabomb/msb-admin.git
cd msb-admin

# 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

通过环境变量设置 MicroSandbox SDK 地址：

```bash
export MSB_ENDPOINT=http://localhost:16379
```

或者直接修改 `main.py` 中的默认值（搜索 `MSB_ENDPOINT =`）。

### 运行

```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

浏览器打开 http://localhost:8080

### 运行测试

```bash
pytest -v
```

全部 43 个测试用例覆盖每个 API 端点及边界情况。

## Docker

### 构建

```bash
docker build -t msb-admin .
```

### 运行

```bash
docker run -d \
  --name msb-admin \
  -p 8080:8080 \
  -e MSB_ENDPOINT=http://host.docker.internal:16379 \
  msb-admin
```

### 持久化数据

挂载卷以持久化项目文件（开发用）：

```bash
docker run -d \
  --name msb-admin \
  -p 8080:8080 \
  -v $(pwd):/msb-admin \
  -e MSB_ENDPOINT=http://host.docker.internal:16379 \
  msb-admin
```

## API 接口

### 沙箱

| 方法 | 路径 | 说明 |
|--------|------|------|
| GET | `/api/sandboxes` | 列出沙箱（支持 `?offset=&limit=&search=`） |
| GET | `/api/sandboxes/{name}` | 获取沙箱详情 |
| POST | `/api/sandboxes/create` | 创建沙箱 |
| POST | `/api/sandboxes/{name}/start` | 启动沙箱 |
| POST | `/api/sandboxes/{name}/stop` | 停止沙箱 |
| POST | `/api/sandboxes/{name}/kill` | 强杀沙箱 |
| DELETE | `/api/sandboxes/{name}` | 删除沙箱 |
| POST | `/api/sandboxes/batch/{action}` | 批量操作（start/stop/kill/delete） |
| GET | `/api/sandboxes/{name}/exec` | 在沙箱中执行命令 |
| POST | `/api/sandboxes/{name}/exec` | 执行自定义命令 |
| POST | `/api/sandboxes/{name}/drain` | 沙箱排水（安全下线） |
| POST | `/api/sandboxes/{name}/ssh/exec` | SSH 远程执行命令 |
| POST | `/api/sandboxes/{name}/ssh/sftp/read` | SFTP 读取文件 |
| POST | `/api/sandboxes/{name}/ssh/sftp/write` | SFTP 写入文件 |
| POST | `/api/sandboxes/{name}/ssh/sftp/mkdir` | SFTP 创建目录 |
| POST | `/api/sandboxes/{name}/ssh/sftp/remove` | SFTP 删除文件 |
| GET | `/api/metrics/fleet` | 集群级实时指标 |

### 镜像

| 方法 | 路径 | 说明 |
|--------|------|------|
| GET | `/api/images` | 列出所有镜像 |
| POST | `/api/images/pull` | 拉取镜像 |
| POST | `/api/images/prune` | 清理未使用镜像 |

### 快照

| 方法 | 路径 | 说明 |
|--------|------|------|
| GET | `/api/snapshots` | 列出快照 |
| POST | `/api/snapshots/create` | 从沙箱创建快照 |
| GET | `/api/snapshots/{name}/export` | 下载快照文件 |
| POST | `/api/snapshots/import` | 从文件导入快照 |
| POST | `/api/snapshots/reindex` | 重建快照索引 |
| POST | `/api/snapshots/restore` | 从快照恢复沙箱 |
| GET | `/api/snapshots/{name}/verify` | 验证快照完整性 |
| DELETE | `/api/snapshots/{name}` | 删除快照 |

### 卷

| 方法 | 路径 | 说明 |
|--------|------|------|
| GET | `/api/volumes` | 列出卷 |
| POST | `/api/volumes/{volume_id}/fs` | 浏览文件系统 |
| POST | `/api/volumes/{volume_id}/fs/write` | 写入文件内容 |
| POST | `/api/volumes/{volume_id}/fs/mkdir` | 创建目录 |
| POST | `/api/volumes/{volume_id}/fs/remove_file` | 删除文件 |
| POST | `/api/volumes/{volume_id}/fs/upload` | 上传文件 |

### WebSocket

| 路径 | 说明 |
|------|------|
| `/ws/logs/{name}` | 实时沙箱日志 |
| `/ws/metrics/{name}` | 实时沙箱指标（CPU、内存） |
| `/ws/terminal/{name}` | 交互式终端会话 |

### 健康检查

| 方法 | 路径 | 说明 |
|--------|------|------|
| GET | `/api/health` | SDK 连通性健康检查 |

## 架构

```
msb-admin/
├── main.py              # FastAPI 应用（所有路由、处理器、WebSocket）
├── test_app.py          # 43 个 pytest 测试用例
├── requirements.txt     # Python 依赖
├── pytest.ini           # 测试配置
├── templates/           # Jinja2 HTML 模板
│   ├── index.html       # 仪表盘首页（含搜索框）
│   ├── sandbox_table.html
│   ├── detail.html      # 沙箱详情（端口、日志、指标、终端）
│   ├── create_form.html
│   ├── fleet_metrics.html
│   ├── logs_panel.html
│   ├── metrics_panel.html
│   ├── terminal.html
│   ├── exec_panel.html
│   ├── exec_result.html
│   ├── fs_panel.html
│   ├── images.html
│   ├── snapshots.html
│   ├── ssh_panel.html
│   └── volumes.html
├── static/              # 静态资源
└── .gitignore
```

### 关键设计决策

- **纯服务端渲染** — 使用 HTMX 做动态更新，无 JavaScript 框架
- **SDK 超时恢复** — `_with_timeout(coro, timeout, name=, recovery=)` 封装，超时时自动执行清理
- **防过期句柄** — `_safe_connect()` 在 `start()` 后重新获取沙箱句柄，避免竞争条件
- **批量操作** — 单端点 `/api/sandboxes/batch/{action}` 接收逗号分隔的名称列表
- **导出临时文件** — 隔离到 `/tmp/msb-admin-exports/`，流传输完成及服务启动时自动清理
- **端口格式化** — 内部 `{guest: host}` 字典转为 `"host:guest"` 字符串展示

## 许可证

MIT
