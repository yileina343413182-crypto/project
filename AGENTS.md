# AGENTS.md

## 目的

本仓库是一个面向动漫评论的情感分析与舆情监控系统。
它包括 Python 数据流水线、Flask 后端 API、Vue 3 前端看板、SQLite 存储，以及可选的 LLM 智能推荐模块。

## 快速代理指南

- 以 `README.md` 作为用户可见的安装、架构与使用说明来源。
- 最重要的入口文件是 `run.py`、`backend/app.py` 和 `backend/config.py`。
- 避免无必要更改数据模型或 API 响应结构，除非与当前前端需求保持一致。
- 后端 API 修复优先放在 `backend/api/*.py`，服务逻辑放在 `backend/services/*.py`。

## 运行与测试命令

修改或测试项目时可使用以下流程：

- Python 环境（虚拟环境位于项目上级目录）：
  - `D:\毕业设计\.venv\Scripts\activate`（Windows）
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

## 架构概览

- `backend/` 包含 Flask 应用和 API blueprint。
- `frontend/` 包含 Vue 3 + Vite 可视化看板代码。
- `data/` 包含原始、处理后和训练数据，以及 SQLite 数据库文件。
- `models/` 包含模型训练和保存的模型文件。
- `crawler/` 包含 B 站、Bangumi、豆瓣爬虫脚本。

## 关键文件

- `run.py` — 一键启动脚本，负责初始化数据库、检查数据与模型、启动 Flask 后端。
- `backend/app.py` — Flask 应用工厂，注册 blueprint、配置 CORS、统一错误处理、健康检查接口。
- `backend/config.py` — 集中管理数据库路径、模型路径、默认模型、JWT、CORS，以及 LLM 提供商和 API 设置。
- `backend/api/*.py` — 后端路由实现文件。
- `backend/services/llm.py` — AI 推荐与简介生成逻辑；支持外部 LLM 调用和本地降级策略。
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
- `backend/config.py` 读取环境变量 `LLM_PROVIDER`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。
- 如果未配置 `LLM_API_KEY` 或 LLM 调用失败，推荐逻辑会降级为本地模糊匹配。

## 前端说明

- `frontend/package.json` 定义了 `dev`、`build` 和 `preview` 脚本。
- 前端通过 Vite 单独运行，默认期望后端 API 地址为 `http://localhost:5000`。

## 代理注意事项

- 不要假设前端会使用未在此文档中列出的额外后端接口。
- 修改后端 API 时保持响应格式稳定。
- 修改 AI 推荐模块时，保留 `backend/services/llm.py` 中的本地降级逻辑。
- 修改认证相关逻辑时，保留 `backend/api/auth.py` 和 `backend/api/history.py` 的 JWT 处理方式。

## 参考

- 用户可见的安装与架构说明请参考 `README.md`。
