# Agent M2：Redis + Celery 运行说明

M2 将耗时 Agent 从 FastAPI 进程内线程池迁移到 Celery Worker。Redis 负责
任务投递，SQLAlchemy 业务数据库仍是前端轮询状态、会话顺序、
租约和幂等结果的唯一事实来源。推荐图的 LangGraph Checkpoint 默认也使用 Redis。

## 1. 依赖和边界

- Python 3.12，Celery 5.6.2，`langgraph-checkpoint-redis` 0.5.1。
- Checkpointer 需要 RedisJSON 和 RediSearch。Redis 8 已内置；Redis 8 以前必须使用
  Redis Stack，不能使用不带模块的普通 Redis。
- broker、可选 result backend 和 Checkpointer 可以共用数据库 0，并分别使用
  `broker`、`result`、`checkpoint` 键前缀。Redis Cloud 的 `SELECT` 不提供逻辑隔离，
  因此云端必须使用同一个 `/0` URL 加前缀。
- Agent 结果默认不写 Celery result backend；浏览器只读取 SQL 任务状态。只有确有外部
  Celery 消费者时才设置 `CELERY_STORE_RESULTS=true`。
- 正式并发运行使用 MySQL。SQLite 仅保留给单机开发、迁移和测试。

## 2. 本机启动

先安装依赖并启动 Redis 8：

```powershell
pip install -r requirements.txt
docker compose -f compose.agent.yml up -d
alembic upgrade head
```

Windows 本地开发使用 threads pool，分别启动两个 Worker和一个 Beat。仅推荐 Worker
消费 `agent.control`，确保周期恢复任务有消费者：

```powershell
celery -A backend.celery_app:celery_app worker -Q agent.recommendation,agent.control -P threads --concurrency=2 -n recommend-local
celery -A backend.celery_app:celery_app worker -Q agent.opinion -P threads --concurrency=2 -n opinion-local
celery -A backend.celery_app:celery_app beat -l info
```

再启动 API 和前端：

```powershell
python run.py
cd frontend
npm run dev
```

生产 Linux 推荐使用两个独立 prefork Worker/容器，并把 concurrency 分别设为
`RECOMMEND_AGENT_MAX_CONCURRENT`、`OPINION_AGENT_MAX_CONCURRENT`。两者之和不要超过
`AGENT_MAX_CONCURRENT` 规划值。Celery 官方不正式支持 Windows，Windows 命令只用于本机开发。

## 3. 配置

```powershell
$env:REDIS_URL="redis://127.0.0.1:6379/0"
$env:CELERY_BROKER_URL=$env:REDIS_URL
$env:CELERY_RESULT_BACKEND=$env:REDIS_URL
$env:AGENT_REDIS_KEY_PREFIX="anime-agent-local"
$env:CELERY_STORE_RESULTS="false"
$env:CELERY_BROKER_POOL_LIMIT="3"
$env:CELERY_REDIS_MAX_CONNECTIONS="6"
$env:RECOMMEND_CHECKPOINT_BACKEND="redis"
$env:RECOMMEND_CHECKPOINT_REDIS_URL=$env:REDIS_URL
$env:RECOMMEND_CHECKPOINT_SQLITE_FALLBACK="false"
$env:RECOMMEND_CHECKPOINT_TTL_MINUTES="1440"
$env:RECOMMEND_REDIS_MAX_CONNECTIONS="4"
$env:AGENT_STREAM_ENABLED="true"
$env:AGENT_STREAM_TTL_SECONDS="900"
$env:AGENT_STREAM_MAX_EVENTS="256"
$env:AGENT_STREAM_BLOCK_MS="10000"
$env:AGENT_STREAM_MAX_CONNECTIONS="4"
$env:CELERY_VISIBILITY_TIMEOUT="3600"
$env:AGENT_TASK_LEASE_SECONDS="180"
$env:AGENT_TASK_HEARTBEAT_SECONDS="30"
$env:AGENT_TASK_QUEUE_STALE_SECONDS="300"
$env:AGENT_TASK_RECOVERY_INTERVAL_SECONDS="120"
```

`CELERY_VISIBILITY_TIMEOUT` 必须大于正常 Agent 最长运行时间，否则 Redis broker 会在原任务
仍执行时再次投递。任务本身已经按 `source_task_id` 幂等，仍不应把该值设得过短。

`RECOMMEND_CHECKPOINT_SQLITE_FALLBACK` 默认只在 `APP_DEBUG=true` 时开启。生产必须关闭，
以免 Redis 故障时多个 Worker 各自写本机 SQLite，造成检查点分裂。

Agent 流式事件只在 Redis 中短期保存阶段状态和推荐追问的文本增量，完整推荐卡片、
舆情报告、评论证据和最终消息仍从 SQL 任务/会话读取。流式连接失败时前端会继续使用
原有轮询。`AGENT_STREAM_MAX_CONNECTIONS` 是 API 进程允许的同时流连接上限，超出的请求
会降级轮询；Cloud Free 环境不要盲目调大。

## 4. 重启与重复投递语义

- API 先提交 SQL `agent_tasks`，再向 Redis 投递；投递失败会把任务置为 `failed`。
- Worker 用 `worker_id + lease_until` 条件抢占任务，并定期续租；重复投递只有一个 Worker
  能进入 Agent。
- Celery 使用 late ack、`reject_on_worker_lost` 和单槽预取。Worker 异常退出时，Redis 会在
  visibility timeout 后重投递。
- Worker 启动会立即扫描一次；Celery Beat 此后每两分钟扫描遗留 queued 和租约过期
  running 任务并重投递。只能运行一个 Beat 实例。
- 最终 Agent 消息以 `agent_messages.source_task_id` 唯一；消息、任务结果和 SQL 终态在同一
  事务提交。重复投递不会产生第二条回答。

## 5. 运维检查

```powershell
redis-cli ping
celery -A backend.celery_app:celery_app inspect ping
celery -A backend.celery_app:celery_app inspect active
celery -A backend.celery_app:celery_app inspect reserved
```

前端通过鉴权接口 `/api/agent/tasks/{task_id}/events` 读取 NDJSON 增量事件，同时保留
`/api/agent/tasks/{task_id}` 轮询。默认不保存 Celery result；SQL 状态表不能被 result
backend 替代，也不应把 Redis 直接暴露给浏览器。

推荐 Agent 的图片附件保存在业务数据库 `agent_attachments` 中。Celery 任务、Redis broker
和流事件只传 `attachment_id`；Worker 按用户与会话复核归属后，使用统一的 `LLM_MODEL`
执行图片理解，不需要单独配置视觉模型。

Redis Cloud Free 的配置和验收步骤见
[`redis-cloud-free.md`](redis-cloud-free.md)。
