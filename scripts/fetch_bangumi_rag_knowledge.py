# -*- coding: utf-8 -*-
"""从本地 Bangumi CSV 的可靠 subject_id 生成可追溯 RAG 知识源。"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from difflib import SequenceMatcher
import json
import logging
from pathlib import Path
import re
import sys
import time
import unicodedata

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database import get_all_anime
from backend.rag.knowledge import DEFAULT_KNOWLEDGE_PATH, load_knowledge_records


logger = logging.getLogger(__name__)
API_ROOT = "https://api.bgm.tv/v0"
DEFAULT_BINDINGS_PATH = PROJECT_ROOT / "data" / "bangumi_subject_bindings.jsonl"
GENRE_TERMS = {
    "科幻", "机甲", "恋爱", "校园", "悬疑", "推理", "奇幻", "冒险", "日常",
    "喜剧", "战斗", "音乐", "运动", "历史", "职场", "群像", "公路", "魔法",
}
MOOD_TERMS = {
    "轻松", "治愈", "热血", "压抑", "温馨", "搞笑", "紧张", "黑暗", "浪漫",
    "感动", "刺激", "平静", "烧脑", "沉重", "爽快", "温柔",
}
CONFIGURED_VIEWING_PLATFORMS = ("b站", "囧次元")
CONFIGURED_PLATFORM_REGION = "日本"
REVIEWED_NAME_BINDINGS = {
    "银魂'": (11834, "銀魂'", "银魂'", "第二期标题中的撇号不能在归一化时丢失"),
    "Angels of Death": (220566, "殺戮の天使", "杀戮天使", "排除同名词组的战锤 40K 条目"),
    "Fate/stay night [Unlimited Blade Works] 第一季": (
        95225,
        "Fate/stay night [Unlimited Blade Works]",
        "Fate/stay night [Unlimited Blade Works]",
        "排除 2010 年剧场版，绑定 2014 年 TV 第一季",
    ),
    "Re：从零开始的异世界生活 第二季 后半": (
        316247,
        "Re:ゼロから始める異世界生活 2nd season 後半クール",
        "Re：从零开始的异世界生活 第二季 后半部分",
        "后半部分在 Bangumi 中是独立条目",
    ),
    "刀剑神域进击篇：无星之夜": (
        315375,
        "劇場版 ソードアート・オンライン プログレッシブ 星なき夜のアリア",
        "剧场版 刀剑神域 进击篇 无星之夜的咏叹调",
        "排除刀剑神域 TV 本篇，绑定进击篇剧场版",
    ),
    "夏目友人帐 第七季": (
        443676,
        "夏目友人帳 漆",
        "夏目友人帐 柒",
        "Bangumi 使用“漆/柒”而不是“第七季”",
    ),
}


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _title_markers(value: object) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    markers = set()
    if re.search(r"第二季|第\s*2\s*季|2nd\s*season|(?:\s|[。.!！])(?:ii|2)$|(?<=[\u4e00-\u9fff])s$", text):
        markers.add("season2")
    if re.search(r"第三季|第\s*3\s*季|3rd\s*season|(?:\s|[。.!！])(?:iii|3)$", text):
        markers.add("season3")
    if re.search(r"第四季|第\s*4\s*季|4th\s*season|(?:\s|[。.!！])(?:iv|4)$", text):
        markers.add("season4")
    for marker, terms in {
        "movie": ("剧场版", "劇場版"),
        "diary": ("日记", "日記"),
        "side_story": ("外传", "外伝"),
        "first_part": ("前篇", "前編"),
        "second_part": ("后篇", "後編"),
        "final_part": ("终章", "終章"),
        "oad": ("oad",),
        "ova": ("ova",),
    }.items():
        if any(term in text for term in terms):
            markers.add(marker)
    return markers


def _canonical_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"(?:中配|粤配)版", "", text)
    text = re.sub(r"第二季|第\s*2\s*季|2nd\s*season", "", text)
    text = re.sub(r"第三季|第\s*3\s*季|3rd\s*season", "", text)
    text = re.sub(r"第四季|第\s*4\s*季|4th\s*season", "", text)
    text = re.sub(r"(?:\s|[。.!！])(?:ii|2)$|(?<=[\u4e00-\u9fff])s$", "", text)
    text = re.sub(r"(?:\s|[。.!！])(?:iii|3)$", "", text)
    text = re.sub(r"(?:\s|[。.!！])(?:iv|4)$", "", text)
    text = re.sub(r"剧场版|劇場版|日记|日記|外传|外伝|前篇|前編|后篇|後編|终章|終章", "", text)
    core = _normalize(text)
    return core + "|" + ",".join(sorted(_title_markers(value)))


def _csv_subject_map() -> dict[str, set[int]]:
    roots = [
        PROJECT_ROOT / "data" / "processed" / "bangumi_top100",
        PROJECT_ROOT / "data" / "processed",
    ]
    paths = list(roots[0].glob("*.csv")) + list(roots[1].glob("cleaned_bangumi*.csv"))
    result: dict[str, set[int]] = {}
    for path in paths:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle), None) or {}
            title = _normalize(row.get("anime_title"))
            subject_id = int(row.get("subject_id"))
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        if title:
            result.setdefault(title, set()).add(subject_id)
    return result


def _load_bindings(path: Path) -> dict[int, dict]:
    if not path.is_file():
        return {}
    result = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    for raw in lines:
        try:
            item = json.loads(raw)
            result[int(item["anime_id"])] = item
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return result


def reliable_local_subjects(
    bindings_path: Path = DEFAULT_BINDINGS_PATH,
) -> tuple[list[tuple[dict, int]], list[dict]]:
    """优先本地唯一 ID，其次使用已保存并可审计的名称模糊绑定。"""
    subject_map = _csv_subject_map()
    bindings = _load_bindings(bindings_path)
    matched, skipped = [], []
    for anime in get_all_anime():
        subject_ids = subject_map.get(_normalize(anime.get("name")), set())
        if len(subject_ids) == 1:
            matched.append((anime, next(iter(subject_ids))))
        elif int(anime["id"]) in bindings:
            matched.append((anime, int(bindings[int(anime["id"])]["subject_id"])))
        else:
            skipped.append(anime)
    return matched, skipped


def _match_score(local_name: str, candidate: dict) -> float:
    local = _canonical_title(local_name)
    aliases = [
        _canonical_title(candidate.get("name")),
        _canonical_title(candidate.get("name_cn")),
    ]
    aliases = [alias for alias in aliases if alias]
    if not local or not aliases:
        return 0.0
    if local in aliases:
        return 1.0
    score = max(SequenceMatcher(None, local, alias).ratio() for alias in aliases)
    local_core = local.split("|", 1)[0]
    alias_cores = [alias.split("|", 1)[0] for alias in aliases]
    if any(local_core in alias or alias in local_core for alias in alias_cores):
        score = max(score, 0.82)
    local_markers = _title_markers(local_name)
    candidate_markers = _title_markers(candidate.get("name")) | _title_markers(candidate.get("name_cn"))
    score += 0.12 * len(local_markers & candidate_markers)
    score -= 0.12 * len(local_markers - candidate_markers)
    score -= 0.06 * len(candidate_markers - local_markers)
    return max(0.0, min(1.0, score))


def _query_variants(name: str) -> list[str]:
    variants = [str(name or "").strip()]
    normalized_spacing = re.sub(r"[-—_:：,，/]+", " ", variants[0])
    variants.append(re.sub(r"\s+", " ", normalized_spacing).strip())
    parts = [part.strip() for part in re.split(r"[-—_:：,，/]+", variants[0]) if len(part.strip()) >= 4]
    if parts:
        variants.append(max(parts, key=len))
    if "第二季" in variants[0]:
        variants.extend((variants[0].replace("第二季", "2"), variants[0].replace("第二季", "S")))
    if "第三季" in variants[0]:
        variants.append(variants[0].replace("第三季", "3"))
    if "星尘远征军" in variants[0]:
        variants.append(variants[0].replace("的奇妙冒险", ""))
    return list(dict.fromkeys(value for value in variants if value))[:5]


def _confidence(score: float, margin: float) -> str:
    if score >= 0.85 and margin >= 0.08:
        return "high"
    if score >= 0.68 and margin >= 0.04:
        return "medium"
    return "low"


def _search_subjects(session: requests.Session, name: str) -> list[dict]:
    response = session.post(
        f"{API_ROOT}/search/subjects",
        params={"limit": 10, "offset": 0},
        json={"keyword": name, "sort": "match", "filter": {"type": [2]}},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", []) if isinstance(payload, dict) else []


def build_subject_bindings(
    session: requests.Session,
    delay: float = 0.2,
) -> tuple[dict[int, dict], list[dict]]:
    """为全部本地作品记录精确或模糊 subject 绑定，并保留置信度。"""
    subject_map = _csv_subject_map()
    bindings, failed = {}, []
    today = date.today().isoformat()
    for anime in get_all_anime():
        anime_id = int(anime["id"])
        local_name = str(anime.get("name") or "")
        subject_ids = subject_map.get(_normalize(local_name), set())
        if len(subject_ids) == 1:
            subject_id = next(iter(subject_ids))
            bindings[anime_id] = {
                "anime_id": anime_id,
                "local_name": local_name,
                "subject_id": subject_id,
                "matched_name": local_name,
                "matched_name_cn": local_name,
                "binding_method": "csv_exact",
                "match_score": 1.0,
                "runner_up_score": 0.0,
                "score_margin": 1.0,
                "confidence": "high",
                "source": f"local_csv_subject_id:{subject_id}",
                "bound_at": today,
            }
            continue
        reviewed = REVIEWED_NAME_BINDINGS.get(local_name.strip())
        if reviewed:
            subject_id, matched_name, matched_name_cn, review_note = reviewed
            bindings[anime_id] = {
                "anime_id": anime_id,
                "local_name": local_name,
                "subject_id": subject_id,
                "matched_name": matched_name,
                "matched_name_cn": matched_name_cn,
                "binding_method": "bangumi_name_fuzzy_reviewed",
                "match_score": 1.0,
                "runner_up_score": 0.0,
                "score_margin": 1.0,
                "confidence": "high",
                "source": "https://api.bgm.tv/v0/search/subjects",
                "review_note": review_note,
                "bound_at": today,
            }
            continue
        try:
            pooled = {}
            queries = _query_variants(local_name)
            first_candidates = _search_subjects(session, queries[0])
            for index, candidate in enumerate(first_candidates):
                if isinstance(candidate, dict) and candidate.get("id"):
                    pooled[int(candidate["id"])] = (index, candidate)
            first_ranked = sorted(
                (
                    (_match_score(local_name, candidate), index, candidate)
                    for index, candidate in enumerate(first_candidates)
                    if isinstance(candidate, dict) and candidate.get("id")
                ),
                key=lambda item: (-item[0], item[1]),
            )
            first_margin = (
                first_ranked[0][0] - first_ranked[1][0]
                if len(first_ranked) > 1
                else first_ranked[0][0] if first_ranked else 0.0
            )
            needs_variants = (
                not first_ranked
                or _confidence(first_ranked[0][0], first_margin) != "high"
                or bool(_title_markers(local_name))
            )
            for query in queries[1:] if needs_variants else []:
                for index, candidate in enumerate(_search_subjects(session, query)):
                    if not isinstance(candidate, dict) or not candidate.get("id"):
                        continue
                    subject_id = int(candidate["id"])
                    current = pooled.get(subject_id)
                    if current is None or index < current[0]:
                        pooled[subject_id] = (index, candidate)
                time.sleep(max(0, delay))
            ranked = sorted(
                (
                    (
                        min(1.0, _match_score(local_name, candidate) + 0.04 / (index + 1)),
                        index,
                        candidate,
                    )
                    for index, candidate in pooled.values()
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if not ranked:
                raise ValueError("search returned no anime candidates")
            score, _rank, best = ranked[0]
            runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
            margin = max(0.0, score - runner_up)
            subject_id = int(best["id"])
            bindings[anime_id] = {
                "anime_id": anime_id,
                "local_name": local_name,
                "subject_id": subject_id,
                "matched_name": str(best.get("name") or ""),
                "matched_name_cn": str(best.get("name_cn") or ""),
                "binding_method": "bangumi_name_fuzzy",
                "match_score": round(score, 6),
                "runner_up_score": round(runner_up, 6),
                "score_margin": round(margin, 6),
                "confidence": _confidence(score, margin),
                "source": "https://api.bgm.tv/v0/search/subjects",
                "bound_at": today,
            }
        except (requests.RequestException, ValueError, TypeError) as exc:
            failed.append({
                "anime_id": anime_id,
                "local_name": local_name,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            })
    return bindings, failed


def write_bindings(output: Path, bindings: dict[int, dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        json.dumps(bindings[anime_id], ensure_ascii=False, separators=(",", ":"))
        for anime_id in sorted(bindings)
    )
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text + ("\n" if text else ""), encoding="utf-8")
    temporary.replace(output)


def _infobox_value(subject: dict, *keys: str) -> str:
    wanted = set(keys)
    for item in subject.get("infobox") or []:
        if not isinstance(item, dict) or item.get("key") not in wanted:
            continue
        value = item.get("value")
        if isinstance(value, list):
            values = [
                str(part.get("v") if isinstance(part, dict) else part).strip()
                for part in value
            ]
            return "、".join(part for part in values if part)
        return str(value or "").strip()
    return ""


def _request_json(session: requests.Session, path: str):
    response = session.get(f"{API_ROOT}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def _record(session: requests.Session, anime: dict, subject_id: int) -> dict:
    subject = _request_json(session, f"/subjects/{subject_id}")
    characters = _request_json(session, f"/subjects/{subject_id}/characters")
    relations = _request_json(session, f"/subjects/{subject_id}/subjects")
    tags = [
        str(item.get("name") or "").strip()
        for item in subject.get("tags") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    genre_tags = [tag for tag in tags if tag in GENRE_TERMS] or tags[:8]
    mood_tags = [tag for tag in tags if tag in MOOD_TERMS]
    character_types = []
    character_items = []
    for item in characters if isinstance(characters, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        relation = str(item.get("relation") or "角色").strip()
        if name:
            character_items.append({
                "character_id": item.get("id"),
                "name": name,
                "relation": relation,
                "type": item.get("type"),
            })
            if len(character_types) < 8:
                character_types.append(f"{relation}：{name}")
    updated_at = date.today().isoformat()
    subject_source = f"https://api.bgm.tv/v0/subjects/{subject_id}"
    relation_source = f"https://api.bgm.tv/v0/subjects/{subject_id}/subjects"
    relation_items = []
    for item in relations if isinstance(relations, list) else []:
        if not isinstance(item, dict):
            continue
        related_subject_type = item.get("type")
        if related_subject_type != 2:
            continue
        related_name = str(item.get("name_cn") or item.get("name") or "").strip()
        if not related_name:
            continue
        relation_items.append({
            "relation_type": str(item.get("relation") or "关联作品").strip(),
            "related_name": related_name,
            "related_subject_id": item.get("id"),
            "related_subject_type": related_subject_type,
            "source": relation_source,
            "updated_at": updated_at,
        })
    air_date = str(subject.get("date") or "").strip()
    return {
        "anime_id": int(anime["id"]),
        "anime_name": anime.get("name", ""),
        "bangumi_subject_id": subject_id,
        "knowledge": {
            "bangumi_name": str(subject.get("name") or "").strip(),
            "bangumi_name_cn": str(subject.get("name_cn") or "").strip(),
            "summary": str(subject.get("summary") or "").strip(),
            "tags": tags,
            "genres": genre_tags,
            "moods": mood_tags,
            "character_types": character_types,
            "studio": _infobox_value(subject, "动画制作", "制作", "制作公司"),
            "year": air_date[:4] if len(air_date) >= 4 else "",
            "air_date": air_date,
            "episodes": subject.get("total_episodes") or "",
            "work_type": str(subject.get("platform") or "").strip(),
            "rank": subject.get("rank") or "",
            "rating": subject.get("rating") or {},
            "source": subject_source,
            "updated_at": updated_at,
        },
        "characters": character_items,
        "characters_source": f"{subject_source}/characters",
        "relations": relation_items,
        "platform_availability": [
            {
                "platform": platform,
                "region": CONFIGURED_PLATFORM_REGION,
                "status": "verified",
                "source": "project_config:user_provided",
                "verification_basis": "user_provided",
                "updated_at": updated_at,
            }
            for platform in CONFIGURED_VIEWING_PLATFORMS
        ],
    }


def write_knowledge_source(output: Path, records: dict[int, dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    text = "\n".join(
        json.dumps(records[anime_id], ensure_ascii=False, separators=(",", ":"))
        for anime_id in sorted(records)
    )
    temporary.write_text(text + ("\n" if text else ""), encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_KNOWLEDGE_PATH)
    parser.add_argument("--bindings", default=str(DEFAULT_BINDINGS_PATH))
    parser.add_argument("--bind-only", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    bindings_path = Path(args.bindings)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "anime-sentiment-rag/1.0 (Bangumi public API knowledge indexing)",
        "Accept": "application/json",
    })
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "POST")),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    if args.bind_only:
        bindings, failed = build_subject_bindings(session, args.delay)
        if not args.dry_run:
            write_bindings(bindings_path, bindings)
        counts = {
            level: sum(item.get("confidence") == level for item in bindings.values())
            for level in ("high", "medium", "low")
        }
        print(json.dumps({
            "binding_count": len(bindings),
            "fuzzy_binding_count": sum(
                str(item.get("binding_method") or "").startswith("bangumi_name_fuzzy")
                for item in bindings.values()
            ),
            "reviewed_binding_count": sum(
                item.get("binding_method") == "bangumi_name_fuzzy_reviewed"
                for item in bindings.values()
            ),
            "confidence_counts": counts,
            "failed": failed,
            "output": str(bindings_path),
            "dry_run": args.dry_run,
        }, ensure_ascii=False))
        return 1 if failed else 0

    matched, skipped = reliable_local_subjects(bindings_path)
    if args.limit > 0:
        matched = matched[:args.limit]
    print(json.dumps({
        "reliable_matches": len(matched),
        "skipped_without_unique_subject_id": len(skipped),
        "output": str(output),
        "dry_run": args.dry_run,
    }, ensure_ascii=False))
    if args.dry_run:
        return 0

    records = {} if args.refresh else load_knowledge_records(str(output))
    failed = []
    for index, (anime, subject_id) in enumerate(matched, start=1):
        anime_id = int(anime["id"])
        if anime_id in records and not args.refresh:
            continue
        try:
            records[anime_id] = _record(session, anime, subject_id)
            logger.info("[%s/%s] fetched %s", index, len(matched), anime.get("name"))
        except (requests.RequestException, ValueError, TypeError) as exc:
            failed.append({"anime_id": anime_id, "subject_id": subject_id, "error": type(exc).__name__})
            logger.warning("Bangumi knowledge fetch failed for %s: %s", anime.get("name"), exc)
        if index % 20 == 0 or index == len(matched):
            print(json.dumps({
                "progress": index,
                "total": len(matched),
                "saved_in_memory": len(records),
                "failed": len(failed),
            }, ensure_ascii=False), flush=True)
        time.sleep(max(0, args.delay))
    target_output = output if not failed else output.with_name(
        f"{output.stem}.partial{output.suffix}"
    )
    write_knowledge_source(target_output, records)
    print(json.dumps({
        "saved_records": len(records),
        "failed": failed,
        "skipped_names": [anime.get("name", "") for anime in skipped],
        "output": str(target_output),
    }, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
