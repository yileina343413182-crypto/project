# -*- coding: utf-8 -*-
"""Contract tests for immutable, versioned prompt assets."""

from __future__ import annotations

import ast
import unittest
from unittest.mock import patch

import yaml

from backend.prompts.registry import (
    ACTIVE_VERSIONS_PATH,
    PROMPT_ROOT,
    _normalized_template_hash,
    activate_prompt_version,
    get_active_versions,
    get_prompt,
    list_prompt_versions,
    prompt_trace,
)


class PromptRegistryTest(unittest.TestCase):
    def test_template_hash_is_stable_across_line_endings(self):
        self.assertEqual(
            _normalized_template_hash(b"system: one\nuser: two\n"),
            _normalized_template_hash(b"system: one\r\nuser: two\r\n"),
        )

    def test_active_recommendation_version_matches_runtime_template(self):
        active_versions = get_active_versions()
        prompt = get_prompt("recommendation")

        self.assertEqual(
            prompt.version,
            active_versions["recommendation"],
        )
        self.assertEqual(prompt.version, "rag-v6-bounded-reasons")
        self.assertEqual(len(prompt.template_hash), 64)

    def test_historical_prompt_version_remains_addressable(self):
        historical = get_prompt("recommendation", version="rag-v1")
        active = get_prompt("recommendation")

        self.assertEqual(historical.version, "rag-v1")
        self.assertNotEqual(historical.template_hash, active.template_hash)

    def test_trace_uses_the_exact_pinned_template_version_and_hash(self):
        prompt = get_prompt("recommendation", version="rag-v1")
        trace = prompt_trace(
            "recommendation",
            "test-model",
            3,
            False,
            prompt=prompt,
        )

        self.assertEqual(trace["template_version"], "rag-v1")
        self.assertEqual(trace["template_hash"], prompt.template_hash)

    def test_all_runtime_system_prompts_are_versioned_assets(self):
        expected = {
            "opinion_report",
            "recommendation",
            "recommendation_followup",
            "watch_guide",
            "evidence_answering",
            "recommendation_intent",
            "anime_description_search",
            "anime_description_knowledge",
            "anime_comment_summary",
        }
        self.assertTrue(expected.issubset(get_active_versions()))
        for name in expected:
            prompt = get_prompt(name)
            self.assertTrue(prompt.render_system().strip(), name)
            self.assertIn(
                prompt.version,
                list_prompt_versions(name),
            )

    def test_legacy_flat_prompt_templates_are_absent(self):
        for name in (
            "recommendation.yaml",
            "opinion_report.yaml",
            "evidence_answering.yaml",
        ):
            self.assertFalse((PROMPT_ROOT / name).exists(), name)

    def test_activation_only_replaces_active_version_file(self):
        template_paths = tuple(
            path
            for path in PROMPT_ROOT.rglob("*.yaml")
            if path.name not in {"active_versions.yaml", "manifest.yaml"}
        )
        before = {path: path.read_bytes() for path in template_paths}

        with (
            patch(
                "backend.prompts.registry.get_active_versions",
                return_value={
                    **get_active_versions(),
                    "recommendation": "rag-v6-bounded-reasons",
                },
            ),
            patch(
                "backend.prompts.registry.Path.write_text",
            ) as write_mock,
            patch(
                "backend.prompts.registry.os.replace",
            ) as replace_mock,
        ):
            selected = activate_prompt_version(
                "recommendation",
                "rag-v1",
            )

        self.assertEqual(selected.version, "rag-v1")
        written = yaml.safe_load(write_mock.call_args.args[0])
        self.assertEqual(
            written["active_versions"]["recommendation"],
            "rag-v1",
        )
        self.assertEqual(
            replace_mock.call_args.args[1],
            ACTIVE_VERSIONS_PATH,
        )
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in template_paths},
        )

    def test_agent_modules_do_not_embed_system_prompt_literals(self):
        backend_root = PROMPT_ROOT.parents[1]
        paths = (
            backend_root / "agents" / "opinion_agent.py",
            backend_root / "agents" / "recommend_agent.py",
            backend_root / "agents" / "recommend_followup.py",
            backend_root / "agents" / "watch_guide.py",
            backend_root / "agents" / "recommend_graph.py",
            backend_root / "services" / "llm.py",
        )
        violations = []
        for path in paths:
            tree = ast.parse(
                path.read_text(encoding="utf-8-sig"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                    )
                    if any(
                        isinstance(target, ast.Name)
                        and target.id == "SYSTEM_PROMPT"
                        for target in targets
                    ):
                        violations.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "SystemMessage"
                ):
                    for keyword in node.keywords:
                        if (
                            keyword.arg == "content"
                            and isinstance(keyword.value, ast.Constant)
                        ):
                            violations.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Tuple)
                    and len(node.elts) >= 2
                    and isinstance(node.elts[0], ast.Constant)
                    and node.elts[0].value == "system"
                    and isinstance(node.elts[1], ast.Constant)
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
