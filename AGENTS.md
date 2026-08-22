# AGENTS.md

## 目的

本仓库是一个面向动漫评论的情感分析与舆情监控系统。
它包括 Python 数据流水线、FastAPI 后端 API、Vue 3 前端看板、SQLite
存储、RAG 检索，以及基于 LangGraph 的推荐和舆情 Agent。

维护优先级：

1. 不破坏现有功能和 API 契约；
2. 修复可复现问题；
3. 使用最小有效改动，避免无关重构和依赖升级。

## 快速代理指南

- 以 `README.md` 作为用户可见的安装、架构与使用说明来源。
- 最重要的入口文件是 `run.py`、`backend/app.py` 和 `backend/config.py`。
- 避免无必要更改数据模型或 API 响应结构，除非与当前前端需求保持一致。
- 后端路由放在 `backend/api/*.py`；通用服务放在
  `backend/services/*.py`；Agent、RAG 和 Prompt 逻辑分别放在
  `backend/agents/`、`backend/rag/` 和 `backend/prompts/`。
- 修改前先检查调用方、被调用方、数据库事务边界、后台线程和 LangGraph
  状态字段。
- 工作区可能已有用户改动；不得覆盖或清理与当前任务无关的修改。

## 运行与测试命令

修改或测试项目时可使用以下流程：

- Python 环境（虚拟环境位于项目上级目录）：
  - `D:\毕业设计\.venv\Scripts\Activate.ps1`（Windows）
  - 使用前先执行 `D:\毕业设计\.venv\Scripts\python.exe --version`；
    Codex 沙箱可能因无权执行用户目录中的基础 Python 而返回
    `Access is denied` / `Unable to create process`。这不代表 `.venv`
    损坏；应先在正常终端或经批准的非沙箱执行中核验。只有正常终端也失败且
    `pyvenv.cfg` 指向的基础解释器确实不存在时，才修复或重建虚拟环境。
  - `pip install -r requirements.txt`
- 前端依赖：
  - `cd frontend`
  - `npm install`
- 后端启动：
  - `python run.py`
- 前端启动：
  - `cd frontend && npm run dev`
- 生成演示数据：
  - `python generate_demo_data.py`
- 批量情感预测：
  - `python batch_predict.py --model bert --overwrite`
- 训练模型：
  - `python -m models.trainer --model textcnn --data_path data/train/sentiment_train.csv ...`
  - `python -m models.trainer --model bert --data_path data/train/sentiment_train.csv ...`
- 后端完整测试：
  - `python -m unittest discover -s tests -p "test*_unittest.py"`
- Prompt 与 Agent 定向测试：
  - `python -m unittest tests.test_prompt_registry_unittest tests.test_prompt_security_unittest tests.test_recommend_graph_unittest`

涉及 API、Agent memory、RAG 评测或 Checkpointer 的测试应使用主数据库副本和独立
临时 Checkpoint 数据库，不得直接污染 `data/anime_sentiment.db`。测试结束后只
能逐个删除明确路径的临时文件。

## 架构概览

- `backend/` 包含 FastAPI 应用和 API router。
- `backend/agents/` 包含推荐/舆情 Agent、状态 Schema、工具、记忆和任务队列。
- `backend/rag/` 包含 Embedding、Chroma、SQLite 关键词降级、索引和评测。
- `backend/prompts/` 包含不可变 Prompt Registry、active version 和模板哈希。
- `frontend/` 包含 Vue 3 + Vite 可视化看板代码。
- `data/` 包含原始、处理后和训练数据、SQLite 数据库及可重建的 Chroma
  运行数据。
- `models/` 包含模型训练和保存的模型文件。
- `crawler/` 包含 B 站、Bangumi、豆瓣爬虫脚本。

## 关键文件

- `run.py` — 一键启动脚本，负责初始化数据库、检查数据与模型、启动 FastAPI 后端。
- `backend/app.py` — FastAPI 应用工厂，注册 APIRouter、配置 lifespan/CORS、统一错误处理、健康检查接口。
- `backend/config.py` — 集中管理数据库路径、模型路径、默认模型、JWT、CORS，以及 LLM 提供商和 API 设置。
- `backend/api/*.py` — 后端路由实现文件。
- `backend/api/agent.py` — 推荐 Agent 2.0、舆情 Agent、任务和会话 API。
- `backend/agents/recommend_graph.py` — 推荐 `StateGraph`、节点、条件边、
  ToolNode、循环限制和 Checkpointer。
- `backend/agents/recommend_agent.py` — 推荐 Agent 兼容入口。
- `backend/agents/tools.py` — Agent 工具注册表；推荐工具必须保持只读且受候选池约束。
- `backend/agents/prompt_security.py` — 输入、评论、RAG 和工具结果的安全检查。
- `backend/prompts/registry.py` — 不可变 Prompt 加载、哈希校验和 active version
  原子切换。
- `backend/rag/` — RAG 索引、检索、存储和评测实现。
- `backend/services/llm.py` — 旧同步推荐兼容逻辑和通用 LLM 文本服务；
  Recommendation Agent 2.0 的流程不得继续堆积在此文件。
- `generate_demo_data.py` — 生成演示数据的脚本。
- `batch_predict.py` — 对数据库评论执行批量情感预测。
- `models/trainer.py` — TextCNN 和 BERT 模型训练入口。

## API 约定

所有响应遵循统一 JSON 结构：

- `{"code": 200, "msg": "...", "data": ...}`
- 错误也返回相同结构，非 200 时 `data: null`。

主要接口：

- `GET /api/health`
- `GET /api/anime/list`
- `GET /api/comments/<anime_id>`
- `GET /api/sentiment/stats/<anime_id>`
- `GET /api/sentiment/trend/<anime_id>`
- `GET /api/sentiment/scatter/<anime_id>`
- `POST /api/sentiment/predict`
- `GET /api/topics/<anime_id>`
- `GET /api/wordcloud/<anime_id>`
- `POST /api/recommend`
- `POST /api/agent/recommend/start`（JWT 保护）
- `POST /api/agent/recommend/message`（JWT 保护）
- `POST /api/agent/attachments/images`（JWT 保护，推荐 Agent 单图上传）
- `GET /api/agent/attachments/<attachment_id>/content`（JWT 保护）
- `DELETE /api/agent/attachments/<attachment_id>`（JWT 保护，仅未绑定图片）
- `POST /api/agent/opinion/analyze`（JWT 保护）
- `GET /api/agent/tasks/<task_id>`（JWT 保护）
- `GET /api/agent/tasks/<task_id>/events`（JWT 保护，NDJSON流）
- `GET /api/agent/sessions`（JWT 保护）
- `GET /api/agent/sessions/<session_id>`（JWT 保护）
- `DELETE /api/agent/sessions/<session_id>`（JWT 保护）
- `POST /api/rag/index/rebuild`（JWT 保护）
- `POST /api/rag/search`（JWT 保护）
- `GET /api/rag/index/status`（JWT 保护）
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/history/chat`（JWT 保护）
- `GET /api/history/chat`（JWT 保护）
- `DELETE /api/history/chat/<id>`（JWT 保护）

## 数据与模型说明

- SQLite 数据库路径：`data/anime_sentiment.db`。
- 默认情感预测模型：`bert`。
- 保存模型路径：`models/saved/textcnn` 和 `models/saved/bert`。
- `backend/config.py` 按 provider 读取 LLM/Embedding Key、Base URL 和模型配置。
- 如果未配置 LLM Key 或调用失败，Agent 必须返回本地降级结果。
- 如果 Embedding 或 Chroma 不可用，RAG 必须降级为 SQLite 关键词检索。
- `data/chroma/` 和 LangGraph Checkpoint 数据库属于可重建/运行时数据；
  不得与主业务数据库混同，也不得在未确认损坏时清理。

## 前端说明

- `frontend/package.json` 定义了 `dev`、`build` 和 `preview` 脚本。
- 前端通过 Vite 单独运行，默认期望后端 API 地址为 `http://localhost:5000`。

## 代理注意事项

- 不要假设前端会使用未在此文档中列出的额外后端接口。
- 修改后端 API 时保持响应格式稳定。
- 修改推荐 Agent 时保持 `RecommendationResponseSchema`、LLM 成功路径和本地
  fallback 的字段一致。
- 偏好不足时维持多级追问；只有确定性解析的用户回答可以写入持久化偏好。
- `RECOMMEND_TOOLS` 必须通过 `bind_tools + ToolNode` 使用，并保持只读、
  当前候选池约束和工具轮次上限。
- 新增图节点或条件边时同步检查 `AgentState`、`recursion_limit`、
  `step_count/retry_count` 和 Checkpointer 恢复测试。
- 用户输入、历史、评论、RAG 证据和工具结果均是不可信数据；进入模型上下文
  前必须经过现有安全边界，不能绕过 `prompt_security.py`。
- 修改认证相关逻辑时，保留 `backend/api/auth.py` 和 `backend/api/history.py` 的 JWT 处理方式。

## Prompt 工程约束

- 运行时 Prompt 只能从
  `backend/prompts/templates/<prompt_name>/<version>.yaml` 加载。
- 已登记在 `manifest.yaml` 中的版本视为不可变；修改 Prompt 时新建版本，
  不覆盖历史文件。
- `active_versions.yaml` 只负责激活或回滚，切换前必须校验模板哈希。
- Agent 任务必须记录实际 `template_name`、`template_version` 和
  `template_hash`。
- 禁止在 Agent 或 Service Python 文件中重新散落 `SYSTEM_PROMPT` 字面量。

## 文件与数据安全

- 禁止批量删除文件或目录，不得使用 `del /s`、`rd /s`、`rmdir /s`、
  `Remove-Item -Recurse` 或 `rm -rf`。
- 删除前必须通过引用检查确认目标失效；一次只删除一个明确路径的文件。
- 不得删除主数据库、数据库备份、raw/processed/train 语料、用户上传文件或
  用户已有工作区改动，除非用户明确指定。
- 对 Chroma 等派生数据执行清理后，必须说明是否可恢复以及对应重建命令。

## 依赖维护

- `requirements.txt` 以 Python 3.12 为基线。
- 只声明代码直接使用或为关键运行能力锁定的依赖；删除依赖前先搜索全部调用点。
- 不做无关的整套依赖升级。修改 LangChain/LangGraph/Pydantic/Chroma/Torch
  版本时，必须先进行 Python 3.12 解析检查，再运行相关导入检查和完整测试。
- Chroma 与 Transformers 共享 `tokenizers` 约束；调整任一版本时必须验证
  依赖交集。

## 参考

- 用户可见的安装与架构说明请参考 `README.md`。
