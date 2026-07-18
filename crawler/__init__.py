# -*- coding: utf-8 -*-
"""
数据采集模块

包含B站爬虫、豆瓣爬虫、Bangumi爬虫和数据清洗四个子模块。
"""

from crawler.bilibili_crawler import crawl_comments, search_anime, save_to_csv
from crawler.douban_crawler import crawl_douban_comments
from crawler.bangumi_crawler import crawl_comments as crawl_bangumi_comments
from crawler.bangumi_crawler import search_subject as search_bangumi_subject
from crawler.cleaner import clean_dataframe, process_file, load_stopwords

__all__ = [
    "crawl_comments",
    "search_anime",
    "save_to_csv",
    "crawl_douban_comments",
    "crawl_bangumi_comments",
    "search_bangumi_subject",
    "clean_dataframe",
    "process_file",
    "load_stopwords",
]
