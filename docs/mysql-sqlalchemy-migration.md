# FastAPI + MySQL + SQLAlchemy 迁移边界

## 本阶段采用的结构

- 19 张业务表由一套 SQLAlchemy ORM 模型维护。
- FastAPI 请求使用 `AsyncSession` 与 `aiomysql`，每个请求一个会话。
- Agent 线程、RAG 重任务、爬虫和离线脚本继续使用同步 Session 与 PyMySQL，每个线程/任务一个会话。
- 普通 CRUD 使用 ORM；批量写入、聚合和方言 upsert 使用 SQLAlchemy Core。
- `data/langgraph_checkpoints.db` 继续由 LangGraph `SqliteSaver` 管理，不纳入业务库 Alembic，也不随业务表迁移。
- 未提供 MySQL 配置时仍回退到现有 SQLite，便于回归和快速回退。

## 首次 MySQL 切换步骤

1. 备份 `data/anime_sentiment.db` 和 `data/langgraph_checkpoints.db`，记录 SHA256。
2. 在 MySQL 8 创建一个空数据库和最小权限应用账号；字符集使用 `utf8mb4`。
3. 在启动服务的同一终端配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。也可直接配置同步 `DATABASE_URL` 和异步 `ASYNC_DATABASE_URL`。本地开发还可以把同名配置写入仓库根目录的 `.env.mysql.local`；该文件已被 Git 忽略，项目启动时自动读取，且不会覆盖已有的进程环境变量。
4. 创建业务表：

   ```powershell
   & '..\.venv\Scripts\python.exe' -m alembic upgrade head
   ```

5. 在服务停止写入期间迁移 19 张业务表：

   ```powershell
   & '..\.venv\Scripts\python.exe' 'scripts\migrate_sqlite_to_mysql.py'
   ```

   迁移工具要求目标业务表为空，在一个事务中写入，逐表核对行数；遇到源 schema 漂移、非法 JSON、外键错误或目标已有数据时直接停止。它不会读取或修改 LangGraph Checkpoint。

   迁移后执行逐行逐列的只读一致性核验：

   ```powershell
   & '..\.venv\Scripts\python.exe' 'scripts\verify_mysql_migration.py'
   ```

   再执行同步/异步 MySQL 事务运行探测；探测数据会回滚。若开发环境显式使用 SQLite
   Checkpointer，脚本也会确认该文件未变化：

   ```powershell
   & '..\.venv\Scripts\python.exe' 'scripts\verify_mysql_runtime.py'
   ```

6. 启动 Redis 8、推荐/舆情 Celery Worker 和 FastAPI，验证登录、历史、Agent 任务、
   RAG 检索及写后读一致性，再进行前端构建和完整回归。同一会话仍严格串行；两个
   Agent 队列的 Worker concurrency 分别使用对应并发上限。
7. 观察稳定后可分别横向增加 FastAPI 与 Celery Worker。生产必须关闭 SQLite Checkpointer
   降级，确保所有 Worker 共用 Redis Checkpoint；详见 `docs/agent-celery-redis.md`。

## 回退

停止 API 与 Celery Worker 后移除 MySQL 数据库环境变量，使应用重新指向迁移前 SQLite
业务库。Redis Checkpoint 不随业务库切换；切换窗口内若 MySQL 已接受新写入，回退前必须
先决定这些增量如何回灌，不能直接覆盖任一数据库。

## 后续：完全删除 SQLite

只有同时满足以下条件才进入：MySQL 连续稳定运行、备份恢复演练通过、全部业务调用不再依赖 SQLite 兼容路径、Checkpoint 已迁出 SQLite、回退方案已改为 MySQL 备份恢复。

完成顺序应为：迁移 Checkpoint → 停止 SQLite 双轨回退 → 删除 SQLite 配置与依赖 → 搜索并验证零 SQLite 调用 → 删除数据库文件。数据库文件删除必须作为单独、明确授权的最后操作，不应由迁移脚本自动执行。

## 后续：所有脚本异步化

异步化只适用于并发数据库或网络 I/O。模型推理、分词、LDA 和 LangGraph CPU/同步节点不能靠改成 `async def` 提速，仍应在线程池或任务队列运行。

若确有全异步要求，按以下顺序推进：

1. 将 Agent 图节点和 RAG 索引流程改为原生 `ainvoke`/异步节点，并取消同步线程中的数据库访问。
2. 为爬虫和批处理建立 `async_sessionmaker` 边界，分批提交并限制连接并发。
3. 将同步 PyMySQL 调用计数降为零后移除同步 Engine；保留 CPU 工作的 executor 边界。
4. 增加取消、超时、连接池耗尽和并发更新测试，再考虑多 worker。

## 后续：Checkpoint 迁移到 MySQL

LangGraph 官方集成列表当前提供 SQLite、PostgreSQL、MongoDB、Redis、Cosmos DB 和 AWS 后端，没有列出官方 MySQL checkpointer。PyPI 上存在第三方 `langgraph-checkpoint-mysql`，但采用它之前必须单独完成版本兼容和持久化一致性评估，不能直接替换生产 Checkpoint。

评估与迁移至少应覆盖：

- `thread_id`、`checkpoint_ns`、`checkpoint_id`、父 checkpoint、pending writes 和 blob 序列化的完整保真；
- `.setup()` 建表、连接自动提交、同步与异步 Saver 生命周期；
- 当前 LangGraph 版本的 checkpoint conformance 测试和任务中断/恢复测试；
- 停写窗口内导出 SQLite、导入 MySQL、逐 thread 比对最新状态与可恢复节点；
- 保留原 SQLite 只读备份，完成一段观察期后再决定是否删除。

若第三方实现未通过上述验证，优先继续保留 SQLite，或改用 LangGraph 官方支持的生产级后端，而不是自行维护一套 Checkpoint ORM 表。

参考：

- LangGraph 官方 Checkpointer 集成列表：https://docs.langchain.com/oss/python/integrations/checkpointers/index
- 第三方 MySQL Checkpointer 包：https://pypi.org/project/langgraph-checkpoint-mysql/
