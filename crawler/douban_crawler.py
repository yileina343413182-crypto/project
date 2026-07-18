# -*- coding: utf-8 -*-
"""
豆瓣短评数据采集模块

功能：
    1. 采集豆瓣电影/动漫条目的短评数据
    2. 使用BeautifulSoup解析HTML提取评论内容、评分、时间
    3. 支持翻页采集，处理豆瓣反爬机制
    4. 采集结果保存为CSV文件

用法：
    # 采集指定豆瓣条目的短评（subject_id从豆瓣URL中获取）
    python douban_crawler.py --subject_id 1889243 --max_pages 20 --output data/raw/douban_comments.csv

    # 指定排序方式
    python douban_crawler.py --subject_id 1889243 --sort new_score --max_pages 30

注意：
    豆瓣反爬较严格，建议：
    1. 设置合理的Cookie（登录后从浏览器复制）
    2. 控制采集速度，不要过于频繁
    3. 如遇到验证码，需要手动处理后继续

依赖：
    pip install requests beautifulsoup4 pandas lxml
"""

import os
import re
import time
import random
import argparse
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import pandas as pd

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ===================== 常量配置 =====================

# 豆瓣短评页面URL模板
COMMENT_URL_TEMPLATE = "https://movie.douban.com/subject/{subject_id}/comments"

# 随机User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# 豆瓣星级评分映射 (CSS class -> 分值)
RATING_MAP = {
    "allstar50": 5,
    "allstar40": 4,
    "allstar30": 3,
    "allstar20": 2,
    "allstar10": 1,
}


def get_headers(cookie=None):
    """
    构造请求头，包含随机User-Agent和可选Cookie。

    Args:
        cookie: 豆瓣登录Cookie字符串，可提高采集成功率

    Returns:
        dict: 请求头字典
    """
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://movie.douban.com/",
        "Connection": "keep-alive",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def parse_rating(comment_item):
    """
    从评论DOM元素中解析星级评分。

    Args:
        comment_item: BeautifulSoup的评论条目元素

    Returns:
        int: 评分值(1-5)，无评分时返回0
    """
    rating_span = comment_item.select_one("span.comment-info span.rating")
    if rating_span:
        classes = rating_span.get("class", [])
        for cls in classes:
            if cls in RATING_MAP:
                return RATING_MAP[cls]
    return 0


def parse_comment_page(html_content):
    """
    解析豆瓣短评页面HTML，提取所有评论信息。

    Args:
        html_content: 页面HTML字符串

    Returns:
        list: 解析出的评论字典列表，每项包含 content, rating, time, user, votes
    """
    soup = BeautifulSoup(html_content, "lxml")
    comments = []

    # 查找所有评论条目
    comment_items = soup.select("div.comment-item")

    if not comment_items:
        logger.warning("页面中未找到评论条目，可能遇到反爬限制或页面结构变更")
        return comments

    for item in comment_items:
        try:
            # 评论内容
            content_tag = item.select_one("span.short")
            content = content_tag.get_text(strip=True) if content_tag else ""

            # 跳过空评论
            if not content:
                continue

            # 用户名
            user_tag = item.select_one("span.comment-info a")
            user_name = user_tag.get_text(strip=True) if user_tag else ""

            # 评分
            rating = parse_rating(item)

            # 评论时间
            time_tag = item.select_one("span.comment-time")
            comment_time = time_tag.get_text(strip=True) if time_tag else ""

            # 点赞数（有用数）
            votes_tag = item.select_one("span.votes")
            votes = 0
            if votes_tag:
                votes_text = votes_tag.get_text(strip=True)
                if votes_text.isdigit():
                    votes = int(votes_text)

            comments.append({
                "content": content,
                "rating": rating,
                "time": comment_time,
                "user": user_name,
                "votes": votes,
            })

        except (AttributeError, TypeError) as e:
            logger.debug("解析单条评论时出错: %s", e)
            continue

    return comments


def crawl_douban_comments(subject_id, max_pages=20, sort="new_score", cookie=None):
    """
    采集指定豆瓣条目的短评数据。

    Args:
        subject_id: 豆瓣条目ID（从URL中获取，如 https://movie.douban.com/subject/1889243/）
        max_pages: 最大采集页数，默认20页（每页约20条评论）
        sort: 排序方式，"new_score"=热门, "time"=最新
        cookie: 豆瓣登录Cookie字符串，建议提供以提高成功率

    Returns:
        list: 所有采集到的评论数据列表
    """
    all_comments = []
    session = requests.Session()

    logger.info("开始采集豆瓣条目 %s 的短评，最大页数=%d，排序=%s", subject_id, max_pages, sort)

    for page_num in range(max_pages):
        start = page_num * 20  # 豆瓣每页20条，通过start参数翻页

        params = {
            "start": start,
            "limit": 20,
            "status": "P",      # P=看过的评论
            "sort": sort,
        }

        url = COMMENT_URL_TEMPLATE.format(subject_id=subject_id)

        try:
            response = session.get(
                url,
                params=params,
                headers=get_headers(cookie),
                timeout=15
            )

            # 检查HTTP状态码
            if response.status_code == 200:
                pass  # 正常
            elif response.status_code == 403:
                logger.error("第%d页被拒绝访问(403)，可能需要更新Cookie或IP被封", page_num + 1)
                break
            elif response.status_code == 404:
                logger.error("条目 %s 不存在(404)", subject_id)
                break
            else:
                logger.warning("第%d页返回异常状态码: %d", page_num + 1, response.status_code)
                break

            response.encoding = "utf-8"

            # 检测是否被重定向到登录页面
            if "accounts.douban.com/passport/login" in response.url:
                logger.error("被重定向到登录页面，请提供有效的Cookie")
                break

            # 解析页面
            comments = parse_comment_page(response.text)

            if not comments:
                logger.info("第%d页无评论数据，采集结束", page_num + 1)
                break

            # 添加元信息
            for c in comments:
                c["subject_id"] = subject_id

            all_comments.extend(comments)
            logger.info("第%d页采集完成，本页%d条，累计%d条",
                        page_num + 1, len(comments), len(all_comments))

            # 检查是否还有下一页（通过解析页面中的翻页器）
            soup = BeautifulSoup(response.text, "lxml")
            next_link = soup.select_one("a.next")
            if not next_link:
                logger.info("已到达最后一页，采集结束")
                break

            # 随机延时3-6秒，豆瓣反爬较严格，需要更长间隔
            sleep_time = random.uniform(3, 6)
            time.sleep(sleep_time)

        except requests.exceptions.Timeout:
            logger.warning("第%d页请求超时，等待后重试", page_num + 1)
            time.sleep(10)
            continue
        except requests.exceptions.RequestException as e:
            logger.error("第%d页请求失败: %s", page_num + 1, e)
            time.sleep(10)
            continue

    logger.info("采集完成，共获取 %d 条短评", len(all_comments))
    return all_comments


def get_subject_info(subject_id, cookie=None):
    """
    获取豆瓣条目的基本信息（名称、评分等）。

    Args:
        subject_id: 豆瓣条目ID
        cookie: 豆瓣Cookie

    Returns:
        dict: 条目信息字典，包含 title, rating 等；失败返回None
    """
    url = f"https://movie.douban.com/subject/{subject_id}/"
    try:
        response = requests.get(url, headers=get_headers(cookie), timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"

        soup = BeautifulSoup(response.text, "lxml")

        # 提取标题
        title_tag = soup.select_one("span[property='v:itemreviewed']")
        title = title_tag.get_text(strip=True) if title_tag else "未知"

        # 提取评分
        rating_tag = soup.select_one("strong.rating_num")
        rating = rating_tag.get_text(strip=True) if rating_tag else "N/A"

        info = {
            "title": title,
            "rating": rating,
            "subject_id": subject_id,
        }
        logger.info("获取到条目信息: %s (评分: %s)", title, rating)
        return info

    except requests.exceptions.RequestException as e:
        logger.error("获取条目信息失败: %s", e)
        return None


def save_to_csv(comments, output_path):
    """
    将评论数据保存为CSV文件。

    Args:
        comments: 评论数据列表
        output_path: 输出文件路径

    Returns:
        bool: 保存成功返回True，失败返回False
    """
    if not comments:
        logger.warning("没有评论数据可保存")
        return False

    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        df = pd.DataFrame(comments)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        logger.info("评论数据已保存到: %s （共%d条）", output_path, len(comments))
        return True
    except (OSError, PermissionError) as e:
        logger.error("保存CSV文件失败: %s", e)
        return False


# ===================== 命令行入口 =====================

def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(description="豆瓣短评数据采集工具")
    parser.add_argument("--subject_id", type=str, required=True,
                        help="豆瓣条目ID（从URL获取，如 1889243）")
    parser.add_argument("--max_pages", type=int, default=20,
                        help="最大采集页数（默认20，每页约20条）")
    parser.add_argument("--sort", type=str, default="new_score",
                        choices=["new_score", "time"],
                        help="排序方式: new_score=热门, time=最新")
    parser.add_argument("--cookie", type=str, default=None,
                        help="豆瓣登录Cookie（建议提供，可提高采集成功率）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出CSV文件路径")
    args = parser.parse_args()

    # 默认输出路径
    default_output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")
    os.makedirs(default_output_dir, exist_ok=True)

    # 获取条目信息
    info = get_subject_info(args.subject_id, cookie=args.cookie)
    title_safe = ""
    if info:
        title_safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in info["title"])

    # 采集评论
    comments = crawl_douban_comments(
        subject_id=args.subject_id,
        max_pages=args.max_pages,
        sort=args.sort,
        cookie=args.cookie
    )

    # 保存结果
    if comments:
        output_path = args.output or os.path.join(
            default_output_dir, f"douban_{title_safe or args.subject_id}.csv"
        )
        save_to_csv(comments, output_path)
    else:
        logger.warning("未采集到任何评论数据")


if __name__ == "__main__":
    main()
