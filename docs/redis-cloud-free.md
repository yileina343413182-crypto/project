# Redis Cloud Free 接入指南（方案 A）

本文面向个人开发和毕业设计演示：不安装 Docker Desktop，使用 Redis Cloud Free 同时承载
Celery broker 与推荐 Agent 的 LangGraph Checkpoint。业务会话、任务终态和观看指南仍存放在
SQL 数据库，Redis 不是业务事实源。

## 1. 适用范围和限制

- 适合单人开发、低并发联调和答辩演示，不作为正式生产高可用方案。
- Free 当前只有 30 MB、30 个并发连接、100 ops/s 和每月 5 GB 流量，应保持 Worker
  concurrency 较低。
- Free 不提供 TLS、复制和控制台备份。连接是公网明文链路，只能放测试账号、模拟评论和
  可丢弃的 Checkpoint；不要处理真实隐私数据。
- Redis Cloud 上 `SELECT` 是兼容 no-op，`/0`、`/1` 不构成隔离。本项目使用同一个 `/0`
  endpoint，并通过 `AGENT_REDIS_KEY_PREFIX` 区分队列、结果和 Checkpoint。
- 免费数据库删除、休眠、区域网络或 IP 白名单变化都会让 Agent 暂时不可用；SQL 历史数据不受
  影响，恢复 Redis 后孤儿任务会由 Beat 重新扫描。

## 2. 创建数据库

1. 注册 Redis Cloud，创建 Free 数据库。
2. 版本选择当前可用的 Redis 8.2 LTS，区域选择离本机最近的区域。
3. Eviction policy 选择 `noeviction`。内存达到上限时让写入明确失败，避免静默淘汰 Celery
   队列键。
4. 只允许本机当前公网 IP，填写单个 `/32` CIDR。公网 IP 变化后需在控制台更新。
5. 记录 Endpoint、端口、用户名（通常是 `default`）和密码。密码只保存在本机私有配置。
6. 在 Cloud 指标/告警中重点观察内存、连接数、ops/s 和网络流量；建议在内存 70%～80% 时
   预警。

LangGraph Redis Checkpointer 依赖 RedisJSON 和 RediSearch；Redis Cloud 的 Redis 8 数据库可
提供所需能力。

## 3. 写入本机私有配置

复制仓库根目录 `.env.example` 中需要的行到 `.env.mysql.local`。项目启动时会读取后者，
该文件已被 Git 忽略。不要修改并提交 `.env.example` 中的占位符为真实密码。

如果密码包含 `@`、`:`、`/`、`#` 等 URL 特殊字符，先做百分号编码：

```powershell
$encodedPassword = [System.Uri]::EscapeDataString("在此临时粘贴密码")
$redisUrl = "redis://default:${encodedPassword}@YOUR_REDIS_HOST:YOUR_REDIS_PORT/0"
```

然后把同一个 URL 填到以下四项：

```dotenv
REDIS_URL=redis://default:URL_ENCODED_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT/0
CELERY_BROKER_URL=redis://default:URL_ENCODED_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT/0
CELERY_RESULT_BACKEND=redis://default:URL_ENCODED_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT/0
RECOMMEND_CHECKPOINT_REDIS_URL=redis://default:URL_ENCODED_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT/0

AGENT_REDIS_KEY_PREFIX=anime-agent-dev
CELERY_STORE_RESULTS=false
CELERY_BROKER_POOL_LIMIT=3
CELERY_REDIS_MAX_CONNECTIONS=6
CELERY_BROKER_SOCKET_TIMEOUT=10
RECOMMEND_CHECKPOINT_BACKEND=redis
RECOMMEND_CHECKPOINT_SQLITE_FALLBACK=true
RECOMMEND_CHECKPOINT_TTL_MINUTES=1440
RECOMMEND_REDIS_MAX_CONNECTIONS=4
AGENT_STREAM_ENABLED=true
AGENT_STREAM_TTL_SECONDS=900
AGENT_STREAM_MAX_EVENTS=256
AGENT_STREAM_BLOCK_MS=10000
AGENT_STREAM_MAX_CONNECTIONS=4
AGENT_TASK_RECOVERY_INTERVAL_SECONDS=120
```

本机演示保留 `RECOMMEND_CHECKPOINT_SQLITE_FALLBACK=true`，Cloud 短暂不可达时推荐图可降级；
多台 Worker 或生产环境必须设为 `false`，防止各机器产生彼此分裂的本地 Checkpoint。

`AGENT_REDIS_KEY_PREFIX` 应按环境唯一，例如 `anime-agent-hubin-dev`。修改前缀等价于切换到一套
新的 Redis 队列和 Checkpoint；切换时必须同时重启 API、两个 Worker 和 Beat。

流式回答会为每个活动浏览器任务占用一个短期 Redis 读取连接。Free 套餐下保持
`AGENT_STREAM_MAX_CONNECTIONS=4`；达到上限时前端会自动回到 SQL 轮询，不影响最终回答。
文本增量默认15分钟过期且每个任务最多保留256条事件，完整舆情报告和评论证据不会复制到
事件流。

## 4. 启动顺序

先执行数据库迁移：

```powershell
alembic upgrade head
```

再分别打开三个 PowerShell 窗口启动后台进程：

```powershell
celery -A backend.celery_app:celery_app worker -Q agent.recommendation,agent.control -P threads --concurrency=2 -n recommend-cloud
celery -A backend.celery_app:celery_app worker -Q agent.opinion -P threads --concurrency=2 -n opinion-cloud
celery -A backend.celery_app:celery_app beat -l info
```

最后启动 API 和前端：

```powershell
python run.py
cd frontend
npm run dev
```

只运行一个 Beat；多个 Beat 会重复发送恢复扫描。`agent.control` 只需由一个 Worker 消费，上述
命令已让推荐 Worker 承担该队列。上述 `concurrency=2` 以 MySQL 业务库为前提；如果当前仍用
SQLite，请把两个 Worker 都改为 `--concurrency=1`，只用于单机兼容运行。Redis Cloud 不能消除
多个 Worker 同时写 SQLite 的锁竞争，真正的多会话并行仍应先配置 MySQL。

## 5. 验收

1. 先做不写数据的连接和模块探针：

   ```powershell
   python -c "from backend.config import REDIS_URL; from redis import Redis; r=Redis.from_url(REDIS_URL, socket_connect_timeout=10); print('PING', r.ping()); print('JSON', r.execute_command('JSON.GET', 'anime-agent:module-probe')); print('SEARCH', r.execute_command('FT._LIST'))"
   ```

2. 在 Cloud 控制台确认连接数明显低于 30，内存没有持续无界增长。
3. 检查 Worker：

   ```powershell
   celery -A backend.celery_app:celery_app inspect ping
   celery -A backend.celery_app:celery_app inspect active
   celery -A backend.celery_app:celery_app inspect reserved
   ```

4. 新建两个不同会话，分别提交推荐和舆情任务；使用 MySQL 时，它们应能并行进入 running 并
   最终 succeeded。
5. 同一会话快速重复提交时，应返回已有任务或 409，不应生成两条 Agent 回答。
6. 停止一个 Worker，等待任务租约/恢复周期后重新启动；Beat 应把遗留任务重新投递，最终消息
   仍只保存一次。
7. 发起一次推荐 Agent 2.0 对话，确认 Cloud 数据库出现带环境前缀的 `checkpoint` 索引/键；
   Checkpoint 默认 24 小时滑动过期。

## 6. 常见故障

- `Connection refused` / timeout：检查 endpoint、端口、URL 编码、当前公网 IP `/32` 白名单和
  Cloud 数据库状态。
- `NOAUTH`：用户名通常为 `default`；确认密码没有直接把 URL 特殊字符拼进连接串。
- `unknown command JSON.*` 或 `FT.*`：当前数据库不满足 RedisJSON/RediSearch 要求，应重建为
  Redis 8 Cloud 数据库。
- 任务一直 queued：确认至少一个 Worker 消费对应业务队列，同时推荐 Worker消费
  `agent.control`，并确认 Beat 正在运行。
- 内存接近 30 MB：先停止提交，等待 24 小时 Checkpoint TTL 回收；不要改成会静默驱逐队列的
  eviction 策略。若长期不够，应升级 Cloud 套餐或迁移到自管 Redis。

更完整的任务幂等、租约和重启语义见 [`agent-celery-redis.md`](agent-celery-redis.md)。
