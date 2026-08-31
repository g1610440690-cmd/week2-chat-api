# 第 2 周交付：带会话持久化的流式聊天 API

> 学习主题：**FastAPI、SQL、Linux 与 Docker**
> 交付物：流式聊天 API + 会话持久化（PostgreSQL）+ 临时状态（Redis）+ 统一异常/日志 + 10 个接口测试 + Docker Compose 一键启动

---

## 一、功能一览

| 模块 | 能力 |
|---|---|
| 认证 | 注册 / 登录（JWT + PBKDF2 密码哈希，密码不明文存储） |
| 会话 | 创建 / 列表 / 重命名 / 删除 / 消息历史分页 |
| 聊天 | 非流式 JSON 回复 + **SSE 流式输出**（打字机效果） |
| 存储 | PostgreSQL 持久化用户 / 会话 / 消息（连接池）；Redis 做限流与缓存（临时状态） |
| 文件 | 上传（类型 + 大小校验）、/static 静态访问 |
| 工程化 | 统一异常格式、请求日志（含 X-Request-ID）、健康检查、10 个接口测试、Docker Compose |

## 二、技术栈

| 组件 | 用途 |
|---|---|
| FastAPI + Uvicorn | Web 框架 + ASGI 服务器（异步） |
| SQLAlchemy 2.0 (asyncio) | ORM + 异步连接池 |
| PostgreSQL 16 | 主数据库（持久化） |
| Redis 7 | 限流计数、缓存（临时状态） |
| Pydantic v2 | 请求校验 + 响应序列化 |
| PyJWT | 登录令牌 |
| pytest + TestClient | 接口测试 |
| Docker / Compose | 容器化一键启动 |

## 三、目录结构

```
week2-chat-api/
├── app/
│   ├── main.py              # 应用入口：lifespan、中间件、路由注册
│   ├── config.py            # 配置（pydantic-settings 读 .env）
│   ├── database.py          # 异步引擎 + 连接池 + 会话工厂
│   ├── models.py            # ORM 模型：User / ChatSession / Message
│   ├── schemas.py           # Pydantic 校验模型
│   ├── exceptions.py        # 统一异常体系
│   ├── logging_conf.py      # 日志配置（控制台 + 滚动文件）
│   ├── redis_client.py      # Redis 封装（限流/缓存，可降级）
│   ├── core/deps.py         # 依赖注入：get_current_user
│   ├── services/chat_service.py  # 业务逻辑：消息落库 + 流式回复
│   └── routers/             # 路由：auth / sessions / chat / upload
├── tests/                   # 10 个接口测试
├── examples/stream_client.py # SSE 流式客户端示例
├── scripts/init_db.py       # 手动建表脚本
├── docs/                    # 学习笔记 + GitHub 部署指南
├── Dockerfile
├── docker-compose.yml
├── requirements.txt / requirements-dev.txt
└── .env.example             # 复制为 .env 使用
```

## 四、快速开始（Docker 方式，推荐）

> 前提：已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows 用户注意 Docker Desktop 需在设置里开启 WSL2 后端）。

```bash
cd week2-chat-api

# 1. 复制环境配置
cp .env.example .env

# 2. 一键构建并启动：PostgreSQL + Redis + FastAPI
docker compose up -d --build

# 3. 查看服务状态（三个容器都应为 running/healthy）
docker compose ps

# 4. 验证
curl http://localhost:8000/healthz
# => {"status":"ok","db":true,"redis":true}

# 5. 打开交互式 API 文档
# 浏览器访问 http://localhost:8000/docs
```

**完整跑通一次聊天**（终端逐条执行）：

```bash
# ① 注册（返回 token，记下来）
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"secret123"}'

# ② 创建会话（用上面的 token）
curl -X POST http://localhost:8000/sessions \
  -H "Authorization: Bearer <你的token>" \
  -H "Content-Type: application/json" -d '{}'

# ③ 非流式发消息
curl -X POST http://localhost:8000/chat/sessions/<会话id>/messages \
  -H "Authorization: Bearer <你的token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"你好，介绍一下 FastAPI"}'

# ④ 流式发消息（-N 表示不缓冲，能看到逐字输出）
curl -N -X POST http://localhost:8000/chat/sessions/<会话id>/stream \
  -H "Authorization: Bearer <你的token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"请用流式输出回答"}'

# ⑤ 交互式流式对话客户端（体验"打字机"效果）
python examples/stream_client.py <你的token>
```

停止服务：`docker compose down`（保留数据）；连数据一起删：`docker compose down -v`。

## 五、不用 Docker 的本地运行（SQLite 模式）

> 适合只想先跑代码、不装 Docker 的场景。数据库换 SQLite、Redis 禁用，功能完整。

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

# macOS / Linux
# python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

把 `.env` 改成：

```env
DATABASE_URL=sqlite+aiosqlite:///./dev.db
REDIS_URL=
```

启动 + 测试：

```bash
uvicorn app.main:app --reload     # 开发模式，改代码自动重启
pytest -v                         # 运行 10 个接口测试
```

## 六、API 一览

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| POST | `/auth/register` | 注册（返回 token） | 否 |
| POST | `/auth/login` | 登录（支持用户名或邮箱） | 否 |
| GET | `/auth/me` | 当前用户信息 | ✅ |
| POST | `/sessions` | 创建会话 | ✅ |
| GET | `/sessions` | 会话列表（含消息数） | ✅ |
| GET | `/sessions/{id}/messages?limit=&offset=` | 消息历史（分页） | ✅ |
| PATCH | `/sessions/{id}` | 重命名会话 | ✅ |
| DELETE | `/sessions/{id}` | 删除会话 | ✅ |
| POST | `/chat/sessions/{id}/messages` | 发消息（非流式） | ✅ |
| POST | `/chat/sessions/{id}/stream` | 发消息（SSE 流式） | ✅ |
| POST | `/upload` | 上传文件（≤5MB，白名单类型） | ✅ |
| GET | `/static/{filename}` | 访问上传的文件 | 否 |
| GET | `/healthz` | 健康检查（DB/Redis） | 否 |
| GET | `/docs` | Swagger 交互式文档 | 否 |

## 七、统一错误格式

所有错误响应都是同一个结构：

```json
{ "code": 40400, "message": "会话不存在", "detail": null }
```

| code | HTTP | 含义 |
|---|---|---|
| 40001 / 40002 | 400 | 文件超限 / 类型不支持 |
| 40100 / 40101 | 401 | 密码错误 / 未登录或 token 失效 |
| 40400 | 404 | 资源不存在（含越权访问，不泄露信息） |
| 40900 | 409 | 用户名或邮箱已注册 |
| 42200 | 422 | 请求参数校验失败（Pydantic） |
| 42900 | 429 | 触发限流（Redis 计数） |
| 50000 | 500 | 服务器内部错误（堆栈只进日志，不返回给客户端） |

## 八、排障手册（掌握标准必会）

### 1. 数据库连接失败（`Connection refused` / `could not connect to server`）
```bash
# 1) 数据库容器还活着吗？
docker compose ps
docker compose logs db          # 看 PostgreSQL 报错

# 2) 应用能连上吗？手动测一下容器内连通性
docker compose exec api python -c "import asyncio,asyncpg; asyncio.run(asyncpg.connect('postgresql://chat:chatpass@db:5432/chatdb', timeout=3))"

# 3) 常见原因
#    - 连接串里主机名写成了 localhost（容器内必须写服务名 db）
#    - 密码/库名和 POSTGRES_PASSWORD / POSTGRES_DB 不一致
#    - api 在 db 就绪前启动 -> 检查 depends_on 的 healthcheck 配置
```

### 2. 端口冲突（`Address already in use` / `bind: address already in use`）
```bash
# 谁占用了 8000 端口？
netstat -ano | findstr :8000        # Windows
ss -tlnp | grep 8000                # Linux

# Windows 下结束占用进程（PID 是上一步最后一列）
taskkill /PID <PID> /F

# Linux 下
lsof -i:8000
kill -9 <PID>

# Docker 场景：把 compose 里 ports 改成别的，如 "8001:8000"
```

### 3. 502 Bad Gateway（通常发生在 nginx 反代 + 应用挂掉时）
```bash
# 1) 应用进程还在吗？
docker compose ps
docker compose logs -f api          # 看应用是否崩溃（栈信息在日志里）

# 2) 应用真的在监听吗？（容器内必须监听 0.0.0.0 而非 127.0.0.1）
docker compose exec api sh -c "ss -tlnp | grep 8000"

# 3) nginx 配置检查（若用了反代）
#    - upstream 的端口要和 uvicorn 实际监听端口一致
#    - 流式接口要关缓冲：proxy_buffering off;
#    - 超时调大：proxy_read_timeout 300s;

# 4) 应用活着但偶发 502 -> 很可能是连接池耗尽或 DB 抖动，看 app 日志里的异常
```

### 4. 其它常见问题
- **改了 .env 不生效**：uvicorn --reload 只监听代码变化，改 .env 需手动重启；Docker 里需 `docker compose up -d --build`（或至少 `docker compose restart api`）。
- **限流 429 拦住了自己**：把 .env 里 `RATE_LIMIT_PER_MINUTE` 调大再重启。
- **流式接口没"打字机"效果**：确认响应头带 `X-Accel-Buffering: no`，且 curl 用了 `-N`。
- **日志在哪**：容器内 `docker compose logs -f api`；本地跑在 `logs/app.log`（滚动，5MB×3）。

## 九、部署到 GitHub

完整的 git 命令、SSH 配置、GitHub Actions 自动测试、常见坑见 **👉 [docs/GitHub部署指南.md](docs/GitHub部署指南.md)**

```bash
# 三行核心命令
git init
git add .
git commit -m "第 2 周交付：带会话持久化的流式聊天 API"
git remote add origin https://github.com/<你的用户名>/week2-chat-api.git
git push -u origin main
```

推送后 GitHub 会自动运行 CI（`.github/workflows/ci.yml`）：安装依赖 → 跑 10 个测试。

## 十、知识速览

| 概念 | 一句话解释 | 在本项目哪里体现 |
|---|---|---|
| 异步 | 单线程内通过事件循环并发处理 IO，不阻塞等待 | 所有路由都是 `async def`；流式回复用异步生成器 |
| 连接池 | 提前建好一批数据库连接反复使用，避免每请求新建 | `app/database.py`：pool_size/max_overflow/pre_ping |
| 缓存 | 把热数据放内存（Redis），减少数据库压力 | 限流计数 + cache_get/cache_set |
| 流式输出 | 边生成边推送，客户端逐块接收 | SSE：`/chat/sessions/{id}/stream` |
| 校验 | 数据进门先验一遍，不合格直接拒绝 | Pydantic `Field(min_length=...)` → 422 |
| 统一异常 | 所有错误同一 JSON 结构，前端好处理 | `app/exceptions.py` 全局 handler |

**详细原理讲解（异步/连接池/缓存/流式/校验/异常/日志/Linux 进程与端口）见 👉 [docs/学习笔记.md](docs/学习笔记.md)**

## 十一、本周掌握标准自测

- [ ] 能解释异步、缓存、连接池各自的用途（对照上表 + 学习笔记）
- [ ] 别人拿到仓库，按 README 15 分钟内能 `docker compose up` 跑起来
- [ ] 能排查 502（看日志、查监听）、端口冲突（netstat/ss + kill）、数据库连接失败（连通性测试）

## 十二、扩展方向（进阶练手）

1. 把 `stream_reply` 换成真实 LLM（OpenAI / DeepSeek），注意流式 API 的调用方式
2. 用 Alembic 做数据库迁移（替代启动时自动建表）
3. 加 WebSocket 实现双向聊天室
4. 写前端页面（EventSource 消费 SSE）
5. 加 pytest 覆盖率报告、Docker 健康检查接入 CI
