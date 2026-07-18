# -*- coding: utf-8 -*-


import json
import logging
import requests
from difflib import SequenceMatcher

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, DB_PATH, LLM_PROVIDER

logger = logging.getLogger(__name__)
TIMEOUT = 20


def _call_llm(messages: list, temperature: float = 0.3, enable_search: bool = False) -> str | None:
    """调用 LLM chat completions 接口（OpenAI 兼容格式）。"""
    if not LLM_API_KEY:
        logger.warning("LLM_API_KEY 未配置，跳过 LLM 调用")
        return None

    try:
        url = f"{LLM_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
                    "model": LLM_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 512,
        }
        if LLM_PROVIDER == "qwen":
                payload["enable_thinking"] = False
                if enable_search:
                    payload["enable_search"] = True
        resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        if not resp.ok:
            logger.warning("LLM HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # 兼容：部分模型仍可能返回 <think>...</think> 标签，剥离后取实际内容
        if "</think>" in content:
            content = content.split("</think>", 1)[-1].strip()
        return content

    except Exception as e:
        logger.warning("LLM 调用失败: %s", e)
        return None


def _local_fuzzy_match(query: str, anime_list: list) -> str | None:
    """本地模糊匹配：计算 query 与动漫名的字符相似度，阈值 0.3。"""
    best_score = 0.0
    best_name = None
    for name in anime_list:
        score = SequenceMatcher(None, query, name).ratio()
        if score > best_score:
            best_score = score
            best_name = name
    return best_name if best_score >= 0.3 else None


def extract_recommendation_intent(query: str, anime_list: list) -> dict:
    """
    使用 LLM 理解用户意图，返回匹配的动漫名和回复文本。
    LLM 不可用时降级为本地模糊匹配。

    Args:
        query: 用户输入
        anime_list: 数据库中所有动漫名列表

    Returns:
        dict: {
            matched_name: str | None,  # 数据库中的动漫名
            reply: str,                # 给用户的自然语言回复
            fallback: bool             # 是否使用了降级匹配
        }
    """
    anime_list_str = "、".join(anime_list) if anime_list else "（暂无数据）"

    system_prompt = (
        "你是一个热情而有品味的动漫推荐助手，只能推荐以下数据库中收录的动漫。\n"
        f"数据库动漫列表：{anime_list_str}\n\n"
        "用户输入可能是动漫名、类型描述或心情。请从列表中选出最匹配的一部动漫。\n\n"
        "reply 编写要求：\n"
        "- 40~70 字，语调自然、有温度，每次风格略有不同（热情、诗意、轻松幽默、一本正经等）\n"
        "- 必须提到该动漫的某个具体亮点（画风、剧情、音乐、角色等）\n"
        "- 不要用「为您推荐」开头，可直接切题或设问\n"
        "- 示例风格 A：「就知道你会喜欢！《命运石之门》的时间线悬念感和科学设定简直续冻。」\n"
        "  示例风格 B：「如果想看带点卧潮感的，《決战属》开头那几话短就把我按在地板上。」\n\n"
        "必须以 JSON 格式回复：\n"
        '{"matched_name": "动漫名（必须是列表原始名称，找不到则 null\uff09",'
        ' "reply": "回复文本"}\n'
        "只输出 JSON，不要其他内容。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # temperature=0.7 增加回复多样性，结构化 JSON 控制保证格式稳定
    llm_output = _call_llm(messages, temperature=0.7)
    if llm_output:
        try:
            text = llm_output.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())
            matched_name = result.get("matched_name")
            reply = result.get("reply", "为您找到了以下推荐：")

            # 验证 matched_name 是否在列表中，否则降级模糊匹配
            if matched_name and matched_name not in anime_list:
                matched_name = (
                    _local_fuzzy_match(matched_name, anime_list)
                    or _local_fuzzy_match(query, anime_list)
                )

            return {"matched_name": matched_name, "reply": reply, "fallback": False}

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("LLM 返回格式解析失败: %s | 原始输出: %s", e, llm_output)

    # 降级：本地模糊匹配
    matched_name = _local_fuzzy_match(query, anime_list)
    if matched_name:
        reply = f"根据您的描述，为您推荐「{matched_name}」："
    else:
        reply = "抱歉，暂时没有找到完全匹配的动漫，为您推荐库中的热门动漫："

    return {"matched_name": matched_name, "reply": reply, "fallback": True}
def generate_anime_description(anime_name: str, anime_id: int = None) -> str:
    """
    生成两段式动漫简介：
    - 第一段（约200字）：LLM 联网搜索生成；联网失败则用 LLM 自身知识生成；
      两者都失败则用评论速览替代。
    - 第二段（约150字）：LLM 根据数据库评论归纳的舆情分析；无评论时省略。

    两段以空行分隔，始终不返回空字符串。
    """
    # ── 第一段：联网搜索 → 自身知识 → 评论速览 ──────────────────
    part1 = _llm_description_with_search(anime_name)
    if not part1:
        part1 = _llm_description_from_knowledge(anime_name)

    # ── 第二段：评论归纳（有评论才生成）────────────────────────
    part2 = ""
    if anime_id is not None:
        comments = _fetch_comments_from_db(anime_id, limit=20)
        if comments:
            part2 = _llm_description_from_comments(anime_name, comments)

    # ── 拼合 ────────────────────────────────────────────────────
    if part1 and part2:
        return f"{part1}\n\n{part2}"
    if part1:
        return part1
    if part2:
        return part2

    # 最终兜底：评论速览
    if anime_id is not None:
        comments = _fetch_comments_from_db(anime_id, limit=3)
        if comments:
            snippets = [c[:40] for c in comments]
            return f"暂无简介。以下是部分观众的评价：「{'」「'.join(snippets)}」"

    return f"暂未收录《{anime_name}》的简介信息。"


def _llm_description_with_search(anime_name: str) -> str:
    """联网搜索生成动漫简介（约200字）。搜索无可信结果时返回空字符串。"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个动漫百科助手。请联网搜索并用 180~220 字的中文介绍该动漫的主要剧情、题材和风格。\n"
                "严格禁止：\n"
                "1. 不能编造任何你没有把握的内容（人物名、剧情细节、制作公司、导演等）\n"
                "2. 不确定的细节直接略去，不要猜测\n"
                "3. 若搜索结果中完全没有关于该动漫的可信信息，只输出：PLEASE_SKIP\n\n"
                "只输出简介正文（或 PLEASE_SKIP），不包含标题、引号或任何其他格式。"
            ),
        },
        {"role": "user", "content": anime_name},
    ]
    result = _call_llm(messages, temperature=0.3, enable_search=True)
    if not result:
        return ""
    text = result.strip()
    if "PLEASE_SKIP" in text:
        logger.info("联网搜索对《%s》无可信结果，降级到自身知识", anime_name)
        return ""
    return text


def _llm_description_from_knowledge(anime_name: str) -> str:
    """用 LLM 自身知识生成动漫简介（约200字）。完全不了解时返回空字符串。"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个动漫百科助手。请凭借你已有的知识，用 180~220 字的中文介绍该动漫的主要剧情、题材和风格。\n"
                "严格禁止：\n"
                "1. 不能编造任何你没有把握的内容（人物名、剧情细节、制作公司等）\n"
                "2. 不确定的细节直接略去，不要猜测\n"
                "3. 如果你对该动漫整体不了解，只输出：PLEASE_SKIP\n\n"
                "只输出简介正文（或 PLEASE_SKIP），不包含标题、引号或任何其他格式。"
            ),
        },
        {"role": "user", "content": anime_name},
    ]
    result = _call_llm(messages, temperature=0.4)
    if not result:
        return ""
    text = result.strip()
    if "PLEASE_SKIP" in text:
        logger.info("LLM 自身知识对《%s》不了解", anime_name)
        return ""
    return text


def _llm_description_from_comments(anime_name: str, comments: list) -> str:
    """根据数据库评论归纳动漫舆情分析（约150字）。"""
    comment_block = "\n".join(f"- {c}" for c in comments)
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个动漫舆情分析助手。下面是观众对某部动漫的真实评论，"
                "请根据这些评论归纳观众的整体评价倾向、反复提及的亮点或槽点，写成 120~160 字的中文分析。\n"
                "要求：\n"
                "1. 只描述评论中有充分依据的内容，不要编造\n"
                "2. 语言客观，可适当带入数据感（如「多数观众认为……」「评论中频繁提到……」）\n"
                "3. 开头用「📊 观众评论分析：」引出\n"
                "只输出分析正文，不含额外标题或引号。"
            ),
        },
        {
            "role": "user",
            "content": f"动漫名：《{anime_name}》\n\n观众评论：\n{comment_block}",
        },
    ]
    result = _call_llm(messages, temperature=0.5)
    return result.strip() if result else ""


def _fetch_comments_from_db(anime_id: int, limit: int = 20) -> list:
    """从 SQLite 数据库读取指定动漫的前 N 条有效评论文本。"""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT content FROM comments WHERE anime_id = ? AND content != '' "
            "ORDER BY likes DESC, id ASC LIMIT ?",
            (anime_id, limit),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0] and len(r[0]) > 5]
    except Exception as e:
        logger.warning("读取评论失败: %s", e)
        return []
