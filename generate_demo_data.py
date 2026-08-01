# -*- coding: utf-8 -*-
"""
演示数据生成脚本

功能：
    为系统注入完整的演示数据，无需爬虫和模型训练，可直接启动前端查看效果。

生成内容：
    - 3 部动漫：进击的巨人 / 鬼灭之刃 / 间谍过家家
    - 每部 500 条评论，含丰富中文词汇（用于词云）
    - 情感分布 positive:neutral:negative ≈ 60:20:20
    - 时间跨度 6 个月（用于折线图趋势）
    - 8 个 LDA 主题（用于主题卡片）
    - 随机 likes、platform 分布

用法：
    python generate_demo_data.py              # 生成演示数据
    python generate_demo_data.py --clear      # 清空所有数据后重新生成
    python generate_demo_data.py --count 200  # 每部动漫生成 200 条（快速模式）
"""

import os
import sys
import random
import logging
import argparse
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.db.models import Anime, Base, Comment, Topic
from backend.db.session import get_sessionmaker, get_sync_engine

random.seed(42)

# ──────────────────────── 动漫定义 ────────────────────────

ANIME_DEFS = [
    {
        "name": "进击的巨人",
        "platform": "bilibili",
        "aliases": ["AOT", "AoT", "巨人", "进巨"],
        "positive_words": [
            "剧情太棒了", "神作", "佳作", "震撼人心", "高燃", "热血", "泪目", "感动",
            "世界观宏大", "剧情反转", "令人窒息", "制作精良", "画质顶级", "配乐完美",
            "演技精湛", "配音给力", "故事深刻", "思想深度", "哲学意味", "值得回味",
            "年度最佳", "十年难遇", "无可挑剔", "良心作品", "细节满满", "伏笔精妙",
            "结局圆满", "人物立体", "主角太强", "艾伦好帅", "进击太帅", "超级推荐",
            "必须追", "不愧是神", "燃到爆炸", "剧情绝了", "剧本出色", "完整度高"
        ],
        "negative_words": [
            "结局烂尾", "大失所望", "剧情崩塌", "虎头蛇尾", "前后矛盾", "人设崩了",
            "拖沓冗长", "剧情拖拉", "打斗无聊", "节奏太慢", "情节混乱", "逻辑问题",
            "最终章差劲", "结局不满意", "人物动机不明", "剧情狗血", "烂结局气死",
            "不如预期", "浪费时间", "过誉了吧", "没有以前好看"
        ],
        "neutral_words": [
            "还行吧", "一般般", "差强人意", "不好不坏", "普普通通", "看过更好的",
            "中规中矩", "需要时间沉淀", "评价不一", "剧情复杂", "需要二刷",
            "第一季比较好", "续集待定", "大家看法不同", "仁者见仁", "慢热型",
            "前几集不好看但后面好了", "我妈不让我追", "太暗黑了有点不适应"
        ],
        "keywords_pool": [
            "巨人", "艾伦", "调查兵团", "自由", "墙壁", "兵长", "历史", "战争",
            "复仇", "人类", "情感", "觉醒", "革命", "地下室", "进击", "马莱",
            "帕拉迪", "始祖巨人", "女巨人", "铠之巨人", "战槌", "颌之巨人",
            "人类的罪", "达到彼岸", "最终季", "漫画结局", "动漫化", "MAPPA",
            "配乐", "泽野弘之", "奥乐米斯", "里维", "赫敏", "尤弥尔"
        ]
    },
    {
        "name": "鬼灭之刃",
        "platform": "bilibili",
        "aliases": ["鬼灭", "KnY", "炭治郎", "demon slayer"],
        "positive_words": [
            "炭治郎太可爱了", "禰豆子好萌", "打斗场面太帅", "ufotable画质炸裂",
            "水之呼吸太美了", "霹雳一闪帅爆", "感人至深", "亲情线好催泪",
            "剧情紧凑", "热血沸腾", "人物设定好", "音乐配乐完美", "声优绝了",
            "遊郭篇最帅", "上弦戏份精彩", "战斗风格多样", "打戏华丽", "必看神作",
            "花之型超美", "坚不移动", "炎之神", "日之呼吸震撼", "泪目了",
            "全员颜值在线", "战斗系最强", "色彩绚丽", "叙事流畅", "人设鲜明",
            "支线精彩", "义气满满", "治愈系战斗", "家人情感线感人"
        ],
        "negative_words": [
            "剧情太简单", "主角光环严重", "反派不够强大", "大哥太废了", "剧情老套",
            "世界观太小", "结局太草率", "战斗逻辑有问题", "打架千篇一律",
            "炭治郎脑残粉太多了", "过誉", "画面华丽但剧情空洞", "没什么深度",
            "比起进击差太远", "看完没什么感觉", "漫画结局太烂"
        ],
        "neutral_words": [
            "还不错", "没有预期中好看", "风格不适合我", "可以看看", "挺好的但不是我菜",
            "普通热血漫", "跟朋友一起看的", "画质确实好", "故事简单但热血",
            "家庭向动漫", "适合小朋友", "无脑爽番", "消遣不错"
        ],
        "keywords_pool": [
            "炭治郎", "禰豆子", "鬼", "鬼杀队", "呼吸法", "水之呼吸", "霹雳一闪",
            "上弦", "下弦", "无惨", "猗窝座", "堕姬", "妓夫太郎", "煤炭",
            "柱", "炎柱", "水柱", "蛇柱", "遊郭", "浅草", "鬼屋", "温泉",
            "日轮刀", "鬼化", "家族", "母亲", "守护", "红眼", "全集中",
            "ufotable", "剧场版", "无限列车", "猩红", "感情"
        ]
    },
    {
        "name": "间谍过家家",
        "platform": "bangumi",
        "aliases": ["SPY×FAMILY", "间谍家家酒", "阿尼亚", "黄昏"],
        "positive_words": [
            "阿尼亚太可爱了", "治愈满分", "欢乐无限", "家庭温馨", "笑点满满",
            "一家三口太甜了", "黄昏帅到爆", "约尔大姐头好强", "剧情轻松愉快",
            "每集都爱看", "阿尼亚的表情包绝了", "暖心故事", "笑中有泪",
            "亲子互动感人", "超级治愈", "假家庭真情感", "反差萌", "心跳加速",
            "黄昏的温柔", "约尔的力气哈哈", "福杰一家", "太甜蜜了", "可爱到犯规",
            "笑死了", "周更太慢了", "一口气看完", "催更", "下一季快来",
            "制作精良", "画面精美", "人设可爱", "故事新颖", "老少皆宜"
        ],
        "negative_words": [
            "剧情太慢", "推进太慢", "没什么惊喜", "有点无聊", "期待更多情节发展",
            "一直这样下去会腻", "太甜了腻味", "主线剧情什么时候发展", "感觉就一直重复",
            "比漫画差很多", "改编一般", "动作戏太少"
        ],
        "neutral_words": [
            "还可以", "适合偶尔看看", "不是我喜欢的类型", "挺萌的", "轻松愉快",
            "当消遣可以", "没看完", "朋友推荐来看的", "治愈系不太合我口味",
            "普通爱情喜剧", "适合家庭观看"
        ],
        "keywords_pool": [
            "阿尼亚", "黄昏", "约尔", "福杰", "读心术", "杀手", "间谍", "家庭",
            "伊甸学院", "任务", "秘密", "超能力", "刺客", "精英", "入学考试",
            "小花生", "邦德", "宠物", "犬", "天才儿童", "伪装", "东西国",
            "洛伊德", "约尔·福杰", "阿尼亚表情包", "可爱", "治愈", "搞笑",
            "家长会", "学校", "马戏团", "叔叔", "组织", "WISE", "白色园丁"
        ]
    }
]

# ──────────────────────── 主题模板 ────────────────────────

# 每部动漫预设 8 个主题（关键词 + 权重）
TOPIC_TEMPLATES = {
    "进击的巨人": [
        {"topic_id": 0, "keywords": [
            {"word": "剧情", "weight": 0.052}, {"word": "进击", "weight": 0.048},
            {"word": "神作", "weight": 0.045}, {"word": "结局", "weight": 0.043},
            {"word": "震撼", "weight": 0.038}, {"word": "感动", "weight": 0.035},
            {"word": "热血", "weight": 0.033}, {"word": "世界观", "weight": 0.030},
            {"word": "必看", "weight": 0.028}, {"word": "反转", "weight": 0.025}
        ]},
        {"topic_id": 1, "keywords": [
            {"word": "艾伦", "weight": 0.065}, {"word": "自由", "weight": 0.058},
            {"word": "历史", "weight": 0.045}, {"word": "复仇", "weight": 0.040},
            {"word": "觉醒", "weight": 0.035}, {"word": "革命", "weight": 0.032},
            {"word": "地下室", "weight": 0.028}, {"word": "马莱", "weight": 0.025},
            {"word": "始祖", "weight": 0.022}, {"word": "巨人化", "weight": 0.020}
        ]},
        {"topic_id": 2, "keywords": [
            {"word": "里维", "weight": 0.060}, {"word": "兵长", "weight": 0.055},
            {"word": "调查兵团", "weight": 0.048}, {"word": "战斗", "weight": 0.042},
            {"word": "帅气", "weight": 0.038}, {"word": "人类", "weight": 0.033},
            {"word": "勇气", "weight": 0.028}, {"word": "牺牲", "weight": 0.025},
            {"word": "英雄", "weight": 0.022}, {"word": "兵团", "weight": 0.018}
        ]},
        {"topic_id": 3, "keywords": [
            {"word": "MAPPA", "weight": 0.050}, {"word": "动画", "weight": 0.045},
            {"word": "画质", "weight": 0.040}, {"word": "配乐", "weight": 0.038},
            {"word": "制作", "weight": 0.035}, {"word": "泽野", "weight": 0.030},
            {"word": "配音", "weight": 0.028}, {"word": "特效", "weight": 0.025},
            {"word": "视觉", "weight": 0.022}, {"word": "音乐", "weight": 0.020}
        ]},
        {"topic_id": 4, "keywords": [
            {"word": "最终季", "weight": 0.055}, {"word": "漫画", "weight": 0.048},
            {"word": "原作", "weight": 0.042}, {"word": "结局", "weight": 0.040},
            {"word": "争议", "weight": 0.035}, {"word": "期待", "weight": 0.032},
            {"word": "完结", "weight": 0.028}, {"word": "改编", "weight": 0.025},
            {"word": "粉丝", "weight": 0.022}, {"word": "解读", "weight": 0.018}
        ]},
        {"topic_id": 5, "keywords": [
            {"word": "巨人", "weight": 0.058}, {"word": "墙壁", "weight": 0.050},
            {"word": "帕拉迪", "weight": 0.044}, {"word": "战争", "weight": 0.038},
            {"word": "灭绝", "weight": 0.033}, {"word": "审判", "weight": 0.028},
            {"word": "种族", "weight": 0.025}, {"word": "政治", "weight": 0.022},
            {"word": "思想", "weight": 0.020}, {"word": "隐喻", "weight": 0.018}
        ]},
        {"topic_id": 6, "keywords": [
            {"word": "米卡莎", "weight": 0.052}, {"word": "阿尔敏", "weight": 0.048},
            {"word": "友情", "weight": 0.042}, {"word": "三人行", "weight": 0.035},
            {"word": "成长", "weight": 0.032}, {"word": "少年", "weight": 0.028},
            {"word": "童年", "weight": 0.025}, {"word": "情感", "weight": 0.022},
            {"word": "记忆", "weight": 0.020}, {"word": "羁绊", "weight": 0.018}
        ]},
        {"topic_id": 7, "keywords": [
            {"word": "哲学", "weight": 0.048}, {"word": "深度", "weight": 0.042},
            {"word": "思考", "weight": 0.038}, {"word": "人性", "weight": 0.035},
            {"word": "权力", "weight": 0.030}, {"word": "道德", "weight": 0.028},
            {"word": "现实", "weight": 0.025}, {"word": "价值观", "weight": 0.022},
            {"word": "意义", "weight": 0.020}, {"word": "存在", "weight": 0.018}
        ]},
    ],
    "鬼灭之刃": [
        {"topic_id": 0, "keywords": [
            {"word": "炭治郎", "weight": 0.068}, {"word": "禰豆子", "weight": 0.060},
            {"word": "热血", "weight": 0.050}, {"word": "感动", "weight": 0.045},
            {"word": "神作", "weight": 0.040}, {"word": "兄妹情", "weight": 0.035},
            {"word": "守护", "weight": 0.030}, {"word": "家人", "weight": 0.028},
            {"word": "人情味", "weight": 0.025}, {"word": "温暖", "weight": 0.022}
        ]},
        {"topic_id": 1, "keywords": [
            {"word": "水之呼吸", "weight": 0.062}, {"word": "打斗", "weight": 0.055},
            {"word": "霹雳一闪", "weight": 0.048}, {"word": "帅气", "weight": 0.042},
            {"word": "技能", "weight": 0.038}, {"word": "日轮刀", "weight": 0.033},
            {"word": "呼吸法", "weight": 0.030}, {"word": "战斗", "weight": 0.028},
            {"word": "全集中", "weight": 0.025}, {"word": "型", "weight": 0.020}
        ]},
        {"topic_id": 2, "keywords": [
            {"word": "ufotable", "weight": 0.058}, {"word": "画质", "weight": 0.052},
            {"word": "作画", "weight": 0.045}, {"word": "精美", "weight": 0.040},
            {"word": "特效", "weight": 0.035}, {"word": "制作", "weight": 0.032},
            {"word": "色彩", "weight": 0.028}, {"word": "流畅", "weight": 0.025},
            {"word": "动画", "weight": 0.022}, {"word": "水准", "weight": 0.018}
        ]},
        {"topic_id": 3, "keywords": [
            {"word": "无惨", "weight": 0.060}, {"word": "上弦", "weight": 0.052},
            {"word": "鬼", "weight": 0.048}, {"word": "猗窝座", "weight": 0.042},
            {"word": "强大", "weight": 0.035}, {"word": "反派", "weight": 0.030},
            {"word": "弦", "weight": 0.028}, {"word": "月", "weight": 0.025},
            {"word": "十二鬼月", "weight": 0.022}, {"word": "血鬼术", "weight": 0.018}
        ]},
        {"topic_id": 4, "keywords": [
            {"word": "遊郭", "weight": 0.055}, {"word": "妓夫太郎", "weight": 0.048},
            {"word": "堕姬", "weight": 0.042}, {"word": "宇髄", "weight": 0.038},
            {"word": "闹鬼", "weight": 0.033}, {"word": "夜", "weight": 0.028},
            {"word": "华丽", "weight": 0.025}, {"word": "遊廓", "weight": 0.022},
            {"word": "风格", "weight": 0.020}, {"word": "章节", "weight": 0.018}
        ]},
        {"topic_id": 5, "keywords": [
            {"word": "无限列车", "weight": 0.058}, {"word": "剧场版", "weight": 0.052},
            {"word": "煤炭", "weight": 0.045}, {"word": "炎柱", "weight": 0.040},
            {"word": "哭了", "weight": 0.035}, {"word": "牺牲", "weight": 0.030},
            {"word": "悲剧", "weight": 0.028}, {"word": "感人", "weight": 0.025},
            {"word": "票房", "weight": 0.022}, {"word": "记录", "weight": 0.018}
        ]},
        {"topic_id": 6, "keywords": [
            {"word": "音乐", "weight": 0.050}, {"word": "配乐", "weight": 0.044},
            {"word": "声优", "weight": 0.038}, {"word": "配音", "weight": 0.033},
            {"word": "曲子", "weight": 0.028}, {"word": "OP", "weight": 0.025},
            {"word": "ED", "weight": 0.022}, {"word": "LiSA", "weight": 0.020},
            {"word": "演唱", "weight": 0.018}, {"word": "燃曲", "weight": 0.015}
        ]},
        {"topic_id": 7, "keywords": [
            {"word": "鬼杀队", "weight": 0.055}, {"word": "柱", "weight": 0.048},
            {"word": "组织", "weight": 0.040}, {"word": "训练", "weight": 0.035},
            {"word": "等级", "weight": 0.030}, {"word": "蝴蝶屋", "weight": 0.028},
            {"word": "选拔", "weight": 0.025}, {"word": "修炼", "weight": 0.022},
            {"word": "成长", "weight": 0.020}, {"word": "等级制度", "weight": 0.018}
        ]},
    ],
    "间谍过家家": [
        {"topic_id": 0, "keywords": [
            {"word": "阿尼亚", "weight": 0.075}, {"word": "可爱", "weight": 0.065},
            {"word": "萌", "weight": 0.058}, {"word": "表情包", "weight": 0.050},
            {"word": "治愈", "weight": 0.045}, {"word": "笑点", "weight": 0.040},
            {"word": "搞笑", "weight": 0.035}, {"word": "欢乐", "weight": 0.030},
            {"word": "太甜了", "weight": 0.025}, {"word": "幸福", "weight": 0.022}
        ]},
        {"topic_id": 1, "keywords": [
            {"word": "黄昏", "weight": 0.065}, {"word": "间谍", "weight": 0.058},
            {"word": "任务", "weight": 0.050}, {"word": "洛伊德", "weight": 0.045},
            {"word": "帅气", "weight": 0.040}, {"word": "伪装", "weight": 0.035},
            {"word": "秘密", "weight": 0.030}, {"word": "WISE", "weight": 0.028},
            {"word": "情报", "weight": 0.025}, {"word": "黄昏行动", "weight": 0.020}
        ]},
        {"topic_id": 2, "keywords": [
            {"word": "约尔", "weight": 0.062}, {"word": "杀手", "weight": 0.055},
            {"word": "力气", "weight": 0.048}, {"word": "大姐头", "weight": 0.042},
            {"word": "美貌", "weight": 0.038}, {"word": "白色园丁", "weight": 0.033},
            {"word": "武器", "weight": 0.028}, {"word": "刺客", "weight": 0.025},
            {"word": "肌肉", "weight": 0.022}, {"word": "奇怪", "weight": 0.018}
        ]},
        {"topic_id": 3, "keywords": [
            {"word": "伊甸学院", "weight": 0.058}, {"word": "入学", "weight": 0.050},
            {"word": "考试", "weight": 0.044}, {"word": "天才", "weight": 0.038},
            {"word": "星", "weight": 0.033}, {"word": "学校", "weight": 0.028},
            {"word": "精英", "weight": 0.025}, {"word": "学霸", "weight": 0.022},
            {"word": "教育", "weight": 0.020}, {"word": "逆天", "weight": 0.018}
        ]},
        {"topic_id": 4, "keywords": [
            {"word": "家庭", "weight": 0.060}, {"word": "温馨", "weight": 0.052},
            {"word": "亲情", "weight": 0.046}, {"word": "假家庭", "weight": 0.040},
            {"word": "真情感", "weight": 0.035}, {"word": "互动", "weight": 0.030},
            {"word": "日常", "weight": 0.028}, {"word": "生活", "weight": 0.025},
            {"word": "一起", "weight": 0.022}, {"word": "笑声", "weight": 0.018}
        ]},
        {"topic_id": 5, "keywords": [
            {"word": "邦德", "weight": 0.055}, {"word": "狗", "weight": 0.048},
            {"word": "宠物", "weight": 0.042}, {"word": "预知", "weight": 0.038},
            {"word": "动物", "weight": 0.033}, {"word": "可爱", "weight": 0.028},
            {"word": "搞笑", "weight": 0.025}, {"word": "宠物狗", "weight": 0.022},
            {"word": "一家", "weight": 0.020}, {"word": "毛茸茸", "weight": 0.018}
        ]},
        {"topic_id": 6, "keywords": [
            {"word": "漫画", "weight": 0.052}, {"word": "原作", "weight": 0.045},
            {"word": "改编", "weight": 0.040}, {"word": "忠实", "weight": 0.035},
            {"word": "还原", "weight": 0.030}, {"word": "感谢", "weight": 0.028},
            {"word": "期待", "weight": 0.025}, {"word": "续集", "weight": 0.022},
            {"word": "更新", "weight": 0.020}, {"word": "催更", "weight": 0.018}
        ]},
        {"topic_id": 7, "keywords": [
            {"word": "动作", "weight": 0.050}, {"word": "喜剧", "weight": 0.044},
            {"word": "悬疑", "weight": 0.038}, {"word": "混合", "weight": 0.033},
            {"word": "类型", "weight": 0.028}, {"word": "风格", "weight": 0.025},
            {"word": "节奏", "weight": 0.022}, {"word": "平衡", "weight": 0.020},
            {"word": "完成度", "weight": 0.018}, {"word": "层次", "weight": 0.015}
        ]},
    ]
}


def generate_comment(anime_def, sentiment, publish_time):
    """生成一条评论"""
    if sentiment == "positive":
        base_phrases = anime_def["positive_words"]
        score = round(random.uniform(0.65, 0.98), 4)
    elif sentiment == "negative":
        base_phrases = anime_def["negative_words"]
        score = round(random.uniform(0.62, 0.90), 4)
    else:  # neutral
        base_phrases = anime_def["neutral_words"]
        score = round(random.uniform(0.55, 0.80), 4)

    # 随机组合评论
    templates = [
        lambda words: random.choice(words),
        lambda words: f"{random.choice(words)}，{random.choice(words)}",
        lambda words: f"{random.choice(words)}！{random.choice(words)}",
        lambda words: f"这部番{random.choice(words)}，{random.choice(words)}",
        lambda words: f"感觉{random.choice(words)}，总体{random.choice(words)}",
        lambda words: f"{random.choice(anime_def['keywords_pool'])}这条线{random.choice(words)}",
        lambda words: f"追了好久终于{random.choice(words)}，{random.choice(words)}",
        lambda words: f"朋友推荐，看完感觉{random.choice(words)}",
        lambda words: f"重刷了一遍，还是{random.choice(words)}",
        lambda words: f"第{random.randint(1,4)}季{random.choice(words)}",
    ]

    content = random.choice(templates)(base_phrases)

    # 偶尔加入关键词
    if random.random() < 0.3:
        kw = random.choice(anime_def["keywords_pool"])
        content = f"{kw}那段{content}"

    return content, score


def init_db(db_path=None):
    """初始化数据库表结构"""
    if db_path:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    engine = get_sync_engine(db_path=db_path)
    Base.metadata.create_all(engine, tables=[Anime.__table__, Comment.__table__, Topic.__table__])
    return get_sessionmaker(db_path=db_path)()


def generate_anime_comments(conn, anime_def, count=500):
    """为一部动漫生成 count 条评论并插入数据库"""
    # 插入或获取动漫记录
    anime_id = conn.scalar(select(Anime.id).where(Anime.name == anime_def["name"]))
    if anime_id is not None:
        logger.info("动漫 [%s] 已存在 (id=%d)，更新评论", anime_def["name"], anime_id)
        conn.execute(delete(Comment).where(Comment.anime_id == anime_id))
    else:
        anime = Anime(name=anime_def["name"], platform=anime_def["platform"])
        conn.add(anime)
        conn.flush()
        anime_id = anime.id
        logger.info("创建动漫 [%s] id=%d", anime_def["name"], anime_id)

    # 情感分布：60% 正面 / 20% 中性 / 20% 负面
    pos_count = int(count * 0.60)
    neu_count = int(count * 0.20)
    neg_count = count - pos_count - neu_count

    sentiments = (
        ["positive"] * pos_count +
        ["neutral"] * neu_count +
        ["negative"] * neg_count
    )
    random.shuffle(sentiments)

    # 时间跨度：最近 6 个月，按正态分布集中在近期
    end_time = datetime.now()
    start_time = end_time - timedelta(days=180)
    total_seconds = int((end_time - start_time).total_seconds())

    records = []
    for label in sentiments:
        offset_sec = int(random.gauss(total_seconds * 0.7, total_seconds * 0.2))
        offset_sec = max(0, min(total_seconds, offset_sec))
        publish_dt = start_time + timedelta(seconds=offset_sec)
        publish_time = publish_dt

        content, score = generate_comment(anime_def, label, publish_time)
        likes = int(random.expovariate(1 / 15))  # 指数分布，大多数 likes 较少
        platform = anime_def["platform"]
        model_used = random.choice(["textcnn", "bert"])

        records.append({
            "anime_id": anime_id,
            "content": content,
            "clean_content": content,
            "publish_time": publish_time,
            "likes": likes,
            "platform": platform,
            "sentiment_label": label,
            "sentiment_score": score,
            "model_used": model_used,
        })

    conn.execute(Comment.__table__.insert(), records)
    conn.commit()
    logger.info("插入 %d 条评论 (anime_id=%d)", len(records), anime_id)
    return anime_id


def generate_topics(conn, anime_id, anime_name):
    """插入预设主题数据"""
    conn.execute(delete(Topic).where(Topic.anime_id == anime_id))

    topics = TOPIC_TEMPLATES.get(anime_name, [])
    records = []
    for t in topics:
        weight = sum(k["weight"] for k in t["keywords"])
        records.append({
            "anime_id": anime_id,
            "topic_id": t["topic_id"],
            "keywords": t["keywords"],
            "weight": round(weight, 6),
        })
    if records:
        conn.execute(Topic.__table__.insert(), records)
    conn.commit()
    logger.info("插入 %d 个主题 (anime_id=%d, name=%s)", len(topics), anime_id, anime_name)


def print_summary(conn):
    """打印生成结果摘要"""
    rows = conn.execute(
        select(
            Anime.id,
            Anime.name,
            Anime.platform,
            func.count(Comment.id),
            func.coalesce(func.sum(case((Comment.sentiment_label == "positive", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Comment.sentiment_label == "neutral", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Comment.sentiment_label == "negative", 1), else_=0)), 0),
        )
        .outerjoin(Comment, Comment.anime_id == Anime.id)
        .group_by(Anime.id, Anime.name, Anime.platform)
        .order_by(Anime.id)
    ).all()

    print(f"\n\033[96m{'─'*68}\033[0m")
    print(f"\033[96m{'ID':<5}{'动漫名称':<18}{'平台':<12}{'评论总数':<10}{'正面':<8}{'中性':<8}{'负面':<8}\033[0m")
    print(f"\033[96m{'─'*68}\033[0m")
    for r in rows:
        print(f"{r[0]:<5}{r[1]:<18}{r[2]:<12}{r[3]:<10}"
              f"\033[92m{r[4]:<8}\033[0m{r[5]:<8}\033[91m{r[6]:<8}\033[0m")

    topic_count = conn.scalar(select(func.count()).select_from(Topic))
    print(f"\033[96m{'─'*68}\033[0m")
    print(f"\033[92m✓ 主题总数: {topic_count} 个\033[0m\n")


def main():
    parser = argparse.ArgumentParser(description="生成演示数据")
    parser.add_argument("--count", type=int, default=500,
                        help="每部动漫生成的评论数量 (默认 500)")
    parser.add_argument("--clear", action="store_true",
                        help="清空所有数据后重新生成")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子 (默认 42)")
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"\n\033[96m\033[1m{'='*55}\033[0m")
    print(f"\033[96m\033[1m  演示数据生成器\033[0m")
    print(f"\033[96m\033[1m  每部动漫: {args.count} 条评论\033[0m")
    print(f"\033[96m\033[1m{'='*55}\033[0m\n")

    conn = init_db()

    if args.clear:
        print("\033[93m  ⚠ 清空所有数据...\033[0m")
        conn.execute(delete(Topic))
        conn.execute(delete(Comment))
        conn.execute(delete(Anime))
        conn.commit()
        print("\033[92m  ✓ 清空完成\033[0m\n")

    for anime_def in ANIME_DEFS:
        print(f"\033[96m▶ 生成《{anime_def['name']}》的演示数据...\033[0m")
        anime_id = generate_anime_comments(conn, anime_def, count=args.count)
        generate_topics(conn, anime_id, anime_def["name"])
        print(f"\033[92m  ✓ 《{anime_def['name']}》完成 (anime_id={anime_id})\033[0m\n")

    print_summary(conn)
    conn.close()

    print(f"""\033[92m\033[1m演示数据生成完成！\033[0m

  现在可以启动系统:
    \033[96mpython run.py\033[0m

  或者分别启动:
    后端: \033[96mpython run_server.py\033[0m
    前端: \033[96mcd frontend && npm run dev\033[0m
""")


if __name__ == "__main__":
    main()
