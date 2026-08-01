"""SQLite-isolated regression checks for synchronous offline ORM callers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from backend.db.models import Anime, Base, Comment, Topic
from backend.db.session import dispose_sync_engines, get_sync_engine, session_scope
from batch_predict import fetch_comments, update_predictions
from crawler.cleaner import get_or_create_anime, init_database, save_to_database
from scripts.migrate_sqlite_to_mysql import migrate_business_tables
from scripts.verify_mysql_migration import compare_business_tables
from topic.keyword_extractor import get_comments_from_db
from topic.lda_model import save_topics_to_db


class OfflineOrmTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(prefix="offline_orm_", suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        Base.metadata.create_all(get_sync_engine(db_path=str(self.path)))

    def tearDown(self):
        dispose_sync_engines()
        self.path.unlink(missing_ok=True)

    def test_crawler_batch_predict_and_keyword_read_share_mapping(self):
        session = init_database(str(self.path))
        anime_id = get_or_create_anime(session, "ORM test", "test")
        inserted = save_to_database(
            session,
            pd.DataFrame([{"content": "测试评论内容", "clean_content": "测试评论内容"}]),
            anime_id,
            "test",
        )
        session.close()

        self.assertEqual(inserted, 1)
        comments = fetch_comments(str(self.path), anime_id=anime_id)
        self.assertEqual(len(comments), 1)
        update_predictions(
            str(self.path),
            [("positive", 0.9, "test-model", comments[0][0])],
        )

        texts, anime_name = get_comments_from_db(anime_id, str(self.path))
        self.assertEqual(anime_name, "ORM test")
        self.assertEqual(texts, ["测试评论内容"])
        with session_scope(db_path=str(self.path)) as check_session:
            comment = check_session.scalar(select(Comment).where(Comment.id == comments[0][0]))
            self.assertEqual(comment.sentiment_label, "positive")

    def test_topic_json_round_trip_uses_shared_json_mapping(self):
        session = init_database(str(self.path))
        anime_id = get_or_create_anime(session, "Topic test", "test")
        session.commit()
        session.close()

        topics = [{"topic_id": 0, "keywords": [{"word": "剧情", "weight": 0.75}]}]
        save_topics_to_db(anime_id, topics, str(self.path))

        with session_scope(db_path=str(self.path)) as session:
            topic = session.scalar(select(Topic).where(Topic.anime_id == anime_id))
            self.assertEqual(topic.keywords, topics[0]["keywords"])
            self.assertAlmostEqual(topic.weight, 0.75)

    def test_sqlite_copy_is_atomic_and_verifies_all_business_tables(self):
        source_session = init_database(str(self.path))
        source_session.add(Anime(id=0, name="Zero id", platform="test"))
        source_session.flush()
        anime_id = get_or_create_anime(source_session, "Migration test", "test")
        source_session.add(Comment(
            anime_id=anime_id,
            content="Relative time",
            publish_time="1d 3h ago",
            platform="test",
            created_at="2026-04-13 11:36:40",
        ))
        source_session.commit()
        source_session.close()

        handle = tempfile.NamedTemporaryFile(prefix="orm_target_", suffix=".db", delete=False)
        handle.close()
        target_path = Path(handle.name)
        target_engine = get_sync_engine(db_path=str(target_path))
        Base.metadata.create_all(target_engine)
        try:
            counts = migrate_business_tables(
                get_sync_engine(db_path=str(self.path)), target_engine, chunk_size=1
            )
            verified = compare_business_tables(
                get_sync_engine(db_path=str(self.path)), target_engine
            )
            self.assertEqual(set(counts), set(Base.metadata.tables))
            self.assertEqual(set(verified), set(Base.metadata.tables))
            self.assertEqual(counts["anime"], 2)
            with session_scope(db_path=str(target_path)) as target_session:
                self.assertEqual(
                    list(target_session.scalars(select(Anime.id).order_by(Anime.id))),
                    [0, 1],
                )
                relative_comment = target_session.scalar(
                    select(Comment).where(Comment.content == "Relative time")
                )
                self.assertEqual(relative_comment.publish_time, "2026-04-12 08:36:40")
        finally:
            target_engine.dispose()
            target_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
