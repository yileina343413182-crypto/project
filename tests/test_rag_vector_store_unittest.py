# -*- coding: utf-8 -*-
"""Chroma 持久化路径选择测试。"""

import os
import unittest

from backend.rag.vector_store import _default_chroma_path


class ChromaPathTest(unittest.TestCase):
    def test_windows_non_ascii_project_uses_local_appdata(self):
        path = _default_chroma_path(
            project_root=r"D:\毕业设计\project",
            platform="nt",
            local_appdata=r"C:\Users\Tester\AppData\Local",
        )

        self.assertEqual(
            path,
            os.path.join(
                r"C:\Users\Tester\AppData\Local",
                "bangumi-agent",
                "chroma",
            ),
        )

    def test_ascii_project_keeps_project_local_path(self):
        path = _default_chroma_path(
            project_root=r"D:\project",
            platform="nt",
            local_appdata=r"C:\Users\Tester\AppData\Local",
        )

        self.assertEqual(path, os.path.join(r"D:\project", "data", "chroma"))

    def test_non_windows_keeps_project_local_path(self):
        path = _default_chroma_path(
            project_root="/srv/动画/project",
            platform="posix",
            local_appdata="/tmp/appdata",
        )

        self.assertEqual(path, os.path.join("/srv/动画/project", "data", "chroma"))


if __name__ == "__main__":
    unittest.main()
