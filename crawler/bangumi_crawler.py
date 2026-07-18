# -*- coding: utf-8 -*-
"""
Bangumi（班固米）评论数据采集模块

功能：
    1. 从Bangumi网页抓取指定动漫条目的吐槽箱（短评）数据
    2. 通过Bangumi API按关键词搜索动漫条目
    3. 支持翻页采集，可设置最大采集页数
    4. 提取字段：评论内容(content)、评分(rate)、发布时间(time)
    5. 采集结果保存为CSV文件到data/raw/目录

用法：
    # 方式一：通过条目ID直接采集（条目ID从 bgm.tv/subject/xxxx 的URL中获取）
    python bangumi_crawler.py --subject_id 253 --max_pages 30

    # 方式二：搜索动漫后交互式选择采集
    python bangumi_crawler.py --search "进击的巨人"

    # 指定输出路径
    python bangumi_crawler.py --subject_id 253 --output data/raw/bangumi_aot.csv

说明：
    - 搜索功能使用 Bangumi API: https://api.bgm.tv/search/subject/{keyword}?type=2
    - 条目详情使用 Bangumi API: https://api.bgm.tv/v0/subjects/{subject_id}
    - 吐槽箱（短评）通过网页抓取: https://bgm.tv/subject/{subject_id}/comments?page=N

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

# Bangumi API 基础地址（用于搜索和条目详情）
BASE_API = "https://api.bgm.tv"
# 搜索API
SEARCH_API = f"{BASE_API}/search/subject"
# 条目详情API (v0)
SUBJECT_API = f"{BASE_API}/v0/subjects"
# 吐槽箱网页地址模板
COMMENTS_URL_TEMPLATE = "https://bgm.tv/subject/{subject_id}/comments"

# 随机User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# Bangumi评分映射（1-10分）
RATING_LABELS = {
    10: "超神作", 9: "神作", 8: "力荐", 7: "推荐", 6: "还行",
    5: "不过不失", 4: "较差", 3: "差", 2: "很差", 1: "不忍直视",
}


def get_headers(accept="text/html"):
    """
    获取带有随机User-Agent的请求头。

    Args:
        accept: Accept头类型，"text/html"用于网页请求，"application/json"用于API请求
    """
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://bgm.tv/",
    }


def search_subject(keyword, subject_type=2, max_results=25):
    """
    通过Bangumi搜索API按关键词搜索动漫条目。

    Args:
        keyword: 搜索关键词，如 "进击的巨人"
        subject_type: 条目类型，2=动画（1=书籍, 2=动画, 3=音乐, 4=游戏, 6=三次元）
        max_results: 最大返回结果数

    Returns:
        list: 包含条目信息的字典列表，每项包含 id, name, name_cn, summary 等
              搜索失败时返回空列表
    """
    url = f"{SEARCH_API}/{keyword}"
    params = {
        "type": subject_type,
        "responseGroup": "small",
        "max_results": max_results,
    }

    try:
        response = requests.get(url, params=params, headers=get_headers(accept="application/json"), timeout=15)
        response.raise_for_status()
        data = response.json()

        results = data.get("list", [])
        if not results:
            logger.warning("搜索 '%s' 未找到结果", keyword)
            return []

        subject_list = []
        for item in results:
            subject_list.append({
                "id": item.get("id"),
                "name": item.get("name", ""),           # 原名（通常是日文）
                "name_cn": item.get("name_cn", ""),      # 中文名
                "summary": item.get("summary", "")[:80],  # 简介截取前80字
                "air_date": item.get("air_date", ""),
                "score": item.get("rating", {}).get("score", 0),
                "rank": item.get("rank", 0),
            })

        logger.info("搜索 '%s' 找到 %d 个条目", keyword, len(subject_list))
        return subject_list

    except requests.exceptions.RequestException as e:
        logger.error("搜索请求失败: %s", e)
        return []


def get_subject_info(subject_id):
    """
    获取Bangumi条目的详细信息（通过API）。

    Args:
        subject_id: Bangumi条目ID

    Returns:
        dict: 条目详细信息，失败返回None
    """
    url = f"{SUBJECT_API}/{subject_id}"
    try:
        response = requests.get(url, headers=get_headers(accept="application/json"), timeout=15)
        response.raise_for_status()
        data = response.json()

        info = {
            "id": data.get("id"),
            "name": data.get("name", ""),
            "name_cn": data.get("name_cn", ""),
            "summary": data.get("summary", ""),
            "score": data.get("rating", {}).get("score", 0),
            "rank": data.get("rating", {}).get("rank", 0),
            "total_comments": data.get("collection", {}).get("doing", 0)
                              + data.get("collection", {}).get("collect", 0),
            "air_date": data.get("date", ""),
            "eps_count": data.get("total_episodes", 0),
            "platform": data.get("platform", ""),
        }

        display_name = info["name_cn"] or info["name"]
        logger.info("获取到条目信息: %s (评分: %s, 排名: #%s)",
                    display_name, info["score"], info["rank"])
        return info

    except requests.exceptions.RequestException as e:
        logger.error("获取条目信息失败: %s", e)
        return None


def parse_comment_page(html_content):
    """
    解析Bangumi吐槽箱页面HTML，提取所有评论信息。

    页面结构：
        #comment_box > .item  每条评论
            a.l                用户名
            span.starlight     评分（class含 starsN, N=1-10）
            small.grey         第一个是"看过"，第二个是时间
            p.comment          评论内容

    Args:
        html_content: 页面HTML字符串

    Returns:
        tuple: (评论列表, 是否有下一页)
    """
    soup = BeautifulSoup(html_content, "lxml")
    comments = []

    items = soup.select("#comment_box .item")
    if not items:
        return comments, False

    for item in items:
        try:
            # 评论内容
            comment_tag = item.select_one("p.comment")
            content = comment_tag.get_text(strip=True) if comment_tag else ""
            if not content:
                continue

            # 用户名
            user_tag = item.select_one("a.l")
            user_name = user_tag.get_text(strip=True) if user_tag else ""

            # 评分（从 span.starlight 的class中提取 starsN）
            rate = 0
            star_span = item.select_one("span.starlight")
            if star_span:
                for cls in star_span.get("class", []):
                    match = re.match(r"stars(\d+)", cls)
                    if match:
                        rate = int(match.group(1))
                        break

            # 时间（第二个 small.grey 标签）
            grey_tags = item.select("small.grey")
            comment_time = ""
            if len(grey_tags) >= 2:
                comment_time = grey_tags[1].get_text(strip=True)
                # 去除 "@ " 前缀
                comment_time = comment_time.lstrip("@ ").strip()

            comments.append({
                "content": content,
                "rate": rate,
                "rate_label": RATING_LABELS.get(rate, "未评分"),
                "time": comment_time,
                "user_name": user_name,
            })

        except (AttributeError, TypeError) as e:
            logger.debug("解析单条评论时出错: %s", e)
            continue

    # 检查是否有下一页：优先查找带 page= 参数的"下一页"箭头链接
    next_page_link = soup.select_one("div.page_inner a.p")
    if next_page_link is None:
        # 兜底：若存在多个页码链接（不止回到第1页），则说明还有下一页
        page_links = soup.select("div.page_inner a[href*='page=']")
        has_next = len(page_links) > 1
    else:
        has_next = True

    return comments, has_next


def crawl_comments(subject_id, max_pages=30):
    """
    采集指定Bangumi条目的吐槽箱（短评）数据。

    通过抓取网页 https://bgm.tv/subject/{id}/comments?page=N 获取评论。

    Args:
        subject_id: Bangumi条目ID
        max_pages: 最大采集页数，默认30页（每页约20条评论）

    Returns:
        list: 包含评论数据的字典列表
    """
    all_comments = []
    session = requests.Session()

    logger.info("开始采集Bangumi条目 %s 的吐槽箱，最大页数=%d", subject_id, max_pages)

    for page_num in range(1, max_pages + 1):
        url = COMMENTS_URL_TEMPLATE.format(subject_id=subject_id)
        params = {"page": page_num}

        try:
            response = session.get(
                url, params=params,
                headers=get_headers(accept="text/html"),
                timeout=15
            )

            if response.status_code == 404:
                logger.error("条目 %s 不存在", subject_id)
                break

            response.raise_for_status()
            response.encoding = "utf-8"

            # 解析页面
            comments, has_next = parse_comment_page(response.text)

            if not comments:
                logger.info("第%d页无评论数据，采集结束", page_num)
                break

            # 添加元信息
            for c in comments:
                c["subject_id"] = subject_id

            all_comments.extend(comments)
            logger.info("第%d页采集完成，本页%d条，累计%d条",
                        page_num, len(comments), len(all_comments))

            # 判断是否还有下一页
            if not has_next or len(comments) < 20:
                logger.info("已到达最后一页，采集结束")
                break

            # 随机延时1-3秒，防止被封
            sleep_time = random.uniform(1, 3)
            time.sleep(sleep_time)

        except requests.exceptions.Timeout:
            logger.warning("第%d页请求超时，跳过该页", page_num + 1)
            time.sleep(3)
            continue
        except requests.exceptions.RequestException as e:
            logger.error("第%d页请求失败: %s", page_num + 1, e)
            time.sleep(5)
            continue

    logger.info("采集完成，共获取 %d 条有效吐槽", len(all_comments))
    return all_comments


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


def search_and_crawl(keyword, max_pages=30, output_dir=None):
    """
    搜索动漫并交互式选择要采集的条目，然后采集吐槽箱。

    Args:
        keyword: 搜索关键词
        max_pages: 最大采集页数
        output_dir: 输出目录，默认 data/raw/

    Returns:
        list: 所有采集到的评论数据
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")

    # 第一步：搜索条目
    subject_list = search_subject(keyword)
    if not subject_list:
        logger.error("未找到相关动漫条目")
        return []

    # 展示搜索结果
    print("\n========== Bangumi搜索结果 ==========")
    for i, subj in enumerate(subject_list):
        cn_name = subj["name_cn"] or subj["name"]
        orig_name = subj["name"] if subj["name_cn"] else ""
        score_str = f"评分:{subj['score']}" if subj['score'] else "暂无评分"
        date_str = subj.get("air_date", "")
        print(f"  [{i}] {cn_name}" +
              (f" ({orig_name})" if orig_name and orig_name != cn_name else "") +
              f"  [{score_str}] {date_str}")
    print("======================================")

    # 用户选择条目
    try:
        choice = int(input("\n请选择条目编号: "))
        if choice < 0 or choice >= len(subject_list):
            logger.error("无效的编号")
            return []
    except ValueError:
        logger.error("请输入有效的数字编号")
        return []

    selected = subject_list[choice]
    subject_id = selected["id"]
    display_name = selected["name_cn"] or selected["name"]

    # 第二步：获取条目详情
    info = get_subject_info(subject_id)
    if info:
        display_name = info["name_cn"] or info["name"]
        print(f"\n条目: {display_name}")
        print(f"评分: {info['score']} | 排名: #{info['rank']} | 放送日期: {info['air_date']}")
        print(f"简介: {info['summary'][:100]}...")

    # 第三步：采集吐槽箱
    logger.info("正在采集《%s》的吐槽箱...", display_name)
    comments = crawl_comments(subject_id, max_pages=max_pages)

    # 给每条评论添加元信息
    for c in comments:
        c["anime_title"] = display_name

    # 保存结果
    if comments:
        safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in display_name)
        output_path = os.path.join(output_dir, f"bangumi_{safe_title}.csv")
        save_to_csv(comments, output_path)

    return comments


# ===================== 命令行入口 =====================

def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(description="Bangumi吐槽箱数据采集工具")
    parser.add_argument("--subject_id", type=int, help="Bangumi条目ID（从 bgm.tv/subject/xxx 获取）")
    parser.add_argument("--search", type=str, help="搜索动漫关键词")
    parser.add_argument("--max_pages", type=int, default=30, help="最大采集页数（默认30）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出CSV文件路径（默认 data/raw/bangumi_条目名.csv）")
    args = parser.parse_args()

    # 默认输出路径
    default_output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")
    os.makedirs(default_output_dir, exist_ok=True)

    if args.search:
        # 搜索模式
        search_and_crawl(args.search, max_pages=args.max_pages, output_dir=default_output_dir)

    elif args.subject_id:
        # 直接采集模式
        info = get_subject_info(args.subject_id)
        comments = crawl_comments(args.subject_id, max_pages=args.max_pages)

        if comments and info:
            display_name = info["name_cn"] or info["name"]
            for c in comments:
                c["anime_title"] = display_name

        if comments:
            if args.output:
                output_path = args.output
            else:
                safe_title = ""
                if info:
                    name = info["name_cn"] or info["name"]
                    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
                output_path = os.path.join(default_output_dir,
                                           f"bangumi_{safe_title or args.subject_id}.csv")
            save_to_csv(comments, output_path)
    else:
        print("请指定 --subject_id 或 --search 参数")
        print("示例:")
        print("  python bangumi_crawler.py --subject_id 253 --max_pages 30")
        print("  python bangumi_crawler.py --search '进击的巨人'")
        parser.print_help()


if __name__ == "__main__":
    main()
