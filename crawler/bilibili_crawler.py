# -*- coding: utf-8 -*-
import os, sys, time, random, hashlib, hmac, argparse, logging
import xml.etree.ElementTree as ET
from datetime import datetime
import requests, pandas as pd

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

INDEX_API = 'https://api.bilibili.com/pgc/season/index/result'
SECTION_API = 'https://api.bilibili.com/pgc/web/season/section'
SEARCH_API = 'https://api.bilibili.com/x/web-interface/search/type'
COMMENT_API = 'https://api.bilibili.com/x/v2/reply'
SUB_REPLY_API = 'https://api.bilibili.com/x/v2/reply/reply'
DANMAKU_API = 'https://api.bilibili.com/x/v1/dm/list.so'
SPI_API = 'https://api.bilibili.com/x/frontend/finger/spi'
TICKET_API = 'https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket'

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(PROJECT_ROOT, 'data', 'raw')


class BiliSession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Referer': 'https://www.bilibili.com',
            'Accept': 'application/json, text/plain, */*',
        })
        self._init_session()

    def _init_session(self):
        logger.info('Initializing B站 session...')
        try:
            self.session.get('https://www.bilibili.com', timeout=15)
        except Exception:
            pass
        # buvid
        try:
            resp = self.session.get(SPI_API, timeout=15)
            spi = resp.json().get('data', {})
            b3, b4 = spi.get('b_3', ''), spi.get('b_4', '')
            if b3:
                self.session.cookies.set('buvid3', b3, domain='.bilibili.com')
            if b4:
                self.session.cookies.set('buvid4', b4, domain='.bilibili.com')
        except Exception as e:
            logger.warning('buvid failed: %s', e)
        # bili_ticket
        try:
            ts = int(time.time())
            hexsign = hmac.new(b'XgwSnGZ1p', ('ts' + str(ts)).encode(), hashlib.sha256).hexdigest()
            resp = self.session.post(TICKET_API,
                params={'key_id': 'ec02', 'hexsign': hexsign, 'context[ts]': str(ts), 'csrf': ''},
                timeout=15)
            td = resp.json()
            if td.get('code') == 0:
                self.session.cookies.set('bili_ticket', td['data']['ticket'], domain='.bilibili.com')
                logger.info('bili_ticket OK')
        except Exception as e:
            logger.warning('bili_ticket failed: %s', e)
        logger.info('Session ready')

    def get(self, url, params=None, **kwargs):
        kwargs.setdefault('timeout', 15)
        return self.session.get(url, params=params, **kwargs)

    def sleep(self, lo=1.5, hi=3.5):
        time.sleep(random.uniform(lo, hi))


def fetch_top_anime(bili, top_n=100):
    all_items, pagesize = [], 20
    pages = (top_n + pagesize - 1) // pagesize
    logger.info('Fetching top %d anime...', top_n)
    for page in range(1, pages + 1):
        params = {'season_type': 1, 'order': 3, 'sort': 0, 'page': page, 'pagesize': pagesize, 'type': 1}
        try:
            resp = bili.get(INDEX_API, params=params)
            data = resp.json()
            if data.get('code') != 0:
                break
            items = data.get('data', {}).get('list', [])
            if not items:
                break
            for it in items:
                all_items.append({
                    'title': it.get('title', ''), 'season_id': it.get('season_id'),
                    'media_id': it.get('media_id'), 'order': it.get('order', ''),
                    'badge': it.get('badge', '')
                })
            logger.info('Page %d: %d items', page, len(items))
            if len(all_items) >= top_n:
                break
            bili.sleep(1, 2)
        except Exception as e:
            logger.error('Ranking page %d error: %s', page, e)
            break
    all_items = all_items[:top_n]
    logger.info('Total: %d anime', len(all_items))
    return all_items


def fetch_episodes(bili, season_id, max_episodes=50):
    try:
        resp = bili.get(SECTION_API, params={'season_id': season_id})
        data = resp.json()
        if data.get('code') != 0:
            return []
        episodes = []
        main_sec = data.get('result', {}).get('main_section', {})
        for ep in main_sec.get('episodes', []):
            episodes.append({
                'aid': ep.get('aid'), 'cid': ep.get('cid'),
                'title': ep.get('title', ''), 'long_title': ep.get('long_title', '')
            })
        total = len(episodes)
        if total > max_episodes:
            logger.info('Season %s: %d eps, taking first %d', season_id, total, max_episodes)
            episodes = episodes[:max_episodes]
        else:
            logger.info('Season %s: %d eps', season_id, total)
        return episodes
    except Exception as e:
        logger.error('Episodes error: %s', e)
        return []


def crawl_danmaku(bili, cid):
    try:
        resp = bili.get(DANMAKU_API, params={'oid': cid})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        dm_list = []
        for dm in root.findall('.//d'):
            text = dm.text or ''
            if not text.strip():
                continue
            attrs = dm.attrib.get('p', '').split(',')
            dm_list.append({
                'content': text,
                'time': float(attrs[0]) if len(attrs) > 0 else 0,
                'mode': int(attrs[1]) if len(attrs) > 1 else 1,
                'color': attrs[3] if len(attrs) > 3 else '',
                'send_time': datetime.fromtimestamp(int(attrs[4])).strftime('%Y-%m-%d %H:%M:%S')
                    if len(attrs) > 4 and attrs[4].isdigit() else '',
                'cid': cid,
            })
        return dm_list
    except Exception as e:
        logger.warning('Danmaku error(cid=%s): %s', cid, e)
        return []


def crawl_comments(bili, aid, max_comments=400):
    all_comments = []
    pn = 1
    while len(all_comments) < max_comments:
        try:
            params = {'type': 1, 'oid': aid, 'pn': pn, 'ps': 20, 'sort': 2}
            resp = bili.get(COMMENT_API, params=params)
            data = resp.json()
            if data.get('code') != 0:
                break
            replies = data.get('data', {}).get('replies') or []
            if not replies:
                break
            for reply in replies:
                all_comments.append(_parse_reply(reply, aid))
                if reply.get('rcount', 0) > 0 and len(all_comments) < max_comments:
                    rpid = reply.get('rpid')
                    subs = _crawl_sub_replies(bili, aid, rpid, max_comments - len(all_comments))
                    all_comments.extend(subs)
                if len(all_comments) >= max_comments:
                    break
            # 判断是否还有更多页
            page_info = data.get('data', {}).get('page', {}) or {}
            total = page_info.get('count', 0)
            if pn * 20 >= total:
                break
            pn += 1
            bili.sleep(0.5, 1.5)
        except Exception as e:
            logger.warning('Comments error(oid=%s): %s', aid, e)
            break
    return all_comments[:max_comments]


def _crawl_sub_replies(bili, aid, root_rpid, limit=100):
    sub_list, pn = [], 1
    while pn <= 20 and len(sub_list) < limit:
        try:
            params = {'type': 1, 'oid': aid, 'root': root_rpid, 'pn': pn, 'ps': 20}
            resp = bili.get(SUB_REPLY_API, params=params)
            data = resp.json()
            if data.get('code') != 0:
                break
            replies = data.get('data', {}).get('replies') or []
            if not replies:
                break
            for r in replies:
                sub_list.append(_parse_reply(r, aid, is_sub=True))
            page_info = data.get('data', {}).get('page', {})
            if pn * 20 >= page_info.get('count', 0):
                break
            pn += 1
            bili.sleep(0.5, 1.5)
        except Exception:
            break
    return sub_list[:limit]


def _parse_reply(reply, aid, is_sub=False):
    member = reply.get('member', {})
    return {
        'content': reply.get('content', {}).get('message', ''),
        'ctime': datetime.fromtimestamp(reply.get('ctime', 0)).strftime('%Y-%m-%d %H:%M:%S')
            if reply.get('ctime') else '',
        'like': reply.get('like', 0),
        'reply_count': reply.get('rcount', 0),
        'user_name': member.get('uname', ''),
        'user_level': member.get('level_info', {}).get('current_level', 0),
        'is_sub_reply': 1 if is_sub else 0,
        'oid': aid,
    }


def search_anime(bili, keyword):
    params = {'search_type': 'media_bangumi', 'keyword': keyword, 'page': 1}
    try:
        resp = bili.get(SEARCH_API, params=params)
        data = resp.json()
        if data.get('code') != 0:
            return []
        results = data.get('data', {}).get('result', [])
        out = []
        for item in results:
            title = item.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
            out.append({
                'title': title, 'season_id': item.get('season_id'),
                'media_id': item.get('media_id'), 'eps_count': item.get('ep_size', 0)
            })
        return out
    except Exception as e:
        logger.error('Search error: %s', e)
        return []


def save_to_csv(data_list, output_path):
    if not data_list:
        return False
    d = os.path.dirname(output_path)
    if d:
        os.makedirs(d, exist_ok=True)
    try:
        df = pd.DataFrame(data_list)
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        logger.info('Saved: %s (%d rows)', output_path, len(data_list))
        return True
    except Exception as e:
        logger.error('Save error: %s', e)
        return False


def safe_filename(text):
    return ''.join(c if c.isalnum() or c in '_-' else '_' for c in text).strip('_')


def crawl_anime(bili, season_id, anime_title='', max_episodes=50,
                max_comments=400, output_dir=None):
    if output_dir is None:
        output_dir = RAW_DIR
    episodes = fetch_episodes(bili, season_id, max_episodes)
    if not episodes:
        return {'danmaku_count': 0, 'comment_count': 0, 'episodes': 0}
    all_dm, all_cm = [], []
    budget = max_comments
    for i, ep in enumerate(episodes):
        logger.info('  [%d/%d] ep%s %s (aid=%s, cid=%s)',
                    i + 1, len(episodes), ep['title'], ep['long_title'], ep['aid'], ep['cid'])
        # danmaku
        dm = crawl_danmaku(bili, ep['cid'])
        for d in dm:
            d['episode'] = ep['title']
            d['episode_title'] = ep['long_title']
            d['anime_title'] = anime_title
            d['aid'] = ep['aid']
        all_dm.extend(dm)
        logger.info('    danmaku: %d', len(dm))
        # comments
        if budget > 0:
            ep_max = min(budget, max(20, max_comments // len(episodes)))
            cm = crawl_comments(bili, ep['aid'], max_comments=ep_max)
            for c in cm:
                c['episode'] = ep['title']
                c['episode_title'] = ep['long_title']
                c['anime_title'] = anime_title
            all_cm.extend(cm)
            budget -= len(cm)
            logger.info('    comments: %d (budget left: %d)', len(cm), max(budget, 0))
        bili.sleep(1, 2.5)
    sf = safe_filename(anime_title)
    if all_dm:
        save_to_csv(all_dm, os.path.join(output_dir, 'bilibili_dm_' + sf + '.csv'))
    if all_cm:
        save_to_csv(all_cm, os.path.join(output_dir, 'bilibili_cm_' + sf + '.csv'))
    return {'danmaku_count': len(all_dm), 'comment_count': len(all_cm), 'episodes': len(episodes)}


def main():
    parser = argparse.ArgumentParser(description='B站番剧弹幕与评论采集工具')
    parser.add_argument('--season_id', type=int, help='番剧season_id')
    parser.add_argument('--search', type=str, help='搜索番剧关键词')
    parser.add_argument('--max_episodes', type=int, default=50)
    parser.add_argument('--max_comments', type=int, default=400)
    parser.add_argument('--output_dir', type=str, default=None)
    args = parser.parse_args()
    bili = BiliSession()
    out = args.output_dir or RAW_DIR
    os.makedirs(out, exist_ok=True)
    if args.search:
        results = search_anime(bili, args.search)
        if not results:
            logger.error('No results')
            return
        print('Search results:')
        for i, a in enumerate(results):
            print('  [%d] %s (%d eps, sid=%s)' % (i, a['title'], a['eps_count'], a['season_id']))
        try:
            choice = int(input('Select: '))
            sel = results[choice]
        except (ValueError, IndexError):
            return
        crawl_anime(bili, sel['season_id'], sel['title'], args.max_episodes, args.max_comments, out)
    elif args.season_id:
        crawl_anime(bili, args.season_id, 'season_%s' % args.season_id,
                    args.max_episodes, args.max_comments, out)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
