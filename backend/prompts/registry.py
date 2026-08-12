# -*- coding: utf-8 -*-
"""PromptOps 注册表：校验不可变提示词资产，并独立切换各模板活动版本。

模板文件的规范化 SHA-256 必须与 manifest 一致；读取后冻结元数据并缓存，
防止运行中被意外修改。版本激活只更新 active_versions 映射。
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Template
from types import MappingProxyType
from typing import Any, Mapping

import yaml


PROMPT_ROOT = Path(__file__).resolve().parent / "templates"
ACTIVE_VERSIONS_PATH = PROMPT_ROOT / "active_versions.yaml"
MANIFEST_PATH = PROMPT_ROOT / "manifest.yaml"
_ACTIVATION_LOCK = threading.Lock()


class PromptRegistryError(RuntimeError):
    """提示词资产、清单或活动版本不符合约定时抛出。"""


def _normalized_template_hash(raw: bytes) -> str:
    """统一换行符后计算哈希，避免 Windows/Unix 换行造成误差。"""
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_prompt_hash(path: str | Path) -> str:
    """计算单个模板文件的规范化 SHA-256。"""
    return _normalized_template_hash(Path(path).read_bytes())


@dataclass(frozen=True)
class PromptTemplate:
    """已验证且不可变的提示词版本及其各个渲染区段。"""
    name: str
    version: str
    system_template: str
    user_template: str
    template_hash: str
    source_path: str
    sections: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def template(self) -> str:
        """Backward-compatible alias for the user template."""
        return self.user_template

    def render(self, **kwargs: Any) -> str:
        return Template(self.user_template).safe_substitute(**kwargs)

    def render_system(self, **kwargs: Any) -> str:
        return Template(self.system_template).safe_substitute(**kwargs)

    def render_section(self, section: str, **kwargs: Any) -> str:
        if section not in self.sections:
            raise PromptRegistryError(
                f"Prompt {self.name}@{self.version} has no section {section}"
            )
        return Template(self.sections[section]).safe_substitute(**kwargs)


def _read_yaml(path: Path) -> dict:
    """读取 YAML，并确保顶层一定是映射。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PromptRegistryError(f"Cannot load prompt config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptRegistryError(f"Prompt config must be a mapping: {path}")
    return data


@lru_cache(maxsize=1)
def _manifest() -> dict[str, dict[str, str]]:
    """读取并冻结模板哈希清单。"""
    data = _read_yaml(MANIFEST_PATH).get("prompts", {})
    if not isinstance(data, dict):
        raise PromptRegistryError("Prompt manifest must contain a prompts mapping")
    result: dict[str, dict[str, str]] = {}
    for name, config in data.items():
        versions = (config or {}).get("versions", {})
        if not isinstance(versions, dict) or not versions:
            raise PromptRegistryError(f"Prompt {name} has no immutable versions")
        result[str(name)] = {
            str(version): str(template_hash)
            for version, template_hash in versions.items()
        }
    return result


def get_active_versions() -> dict[str, str]:
    """返回各模板当前活动版本的普通字典副本。"""
    data = _read_yaml(ACTIVE_VERSIONS_PATH).get("active_versions", {})
    if not isinstance(data, dict):
        raise PromptRegistryError("active_versions.yaml must contain a mapping")
    active = {str(name): str(version) for name, version in data.items()}
    manifest = _manifest()
    for name, version in active.items():
        if name not in manifest or version not in manifest[name]:
            raise PromptRegistryError(
                f"Active prompt version does not exist: {name}@{version}"
            )
    return active


def list_prompt_versions(name: str) -> tuple[str, ...]:
    """列出清单中登记的指定模板全部版本。"""
    versions = _manifest().get(name)
    if versions is None:
        raise KeyError(name)
    return tuple(versions)


@lru_cache(maxsize=None)
def _load_prompt(name: str, version: str) -> PromptTemplate:
    """校验清单、文件哈希和必需字段后加载一个不可变模板。"""
    versions = _manifest().get(name)
    if versions is None or version not in versions:
        raise KeyError(f"{name}@{version}")

    path = PROMPT_ROOT / name / f"{version}.yaml"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptRegistryError(f"Cannot read prompt template {path}: {exc}") from exc
    actual_hash = _normalized_template_hash(raw)
    expected_hash = versions[version]
    if actual_hash != expected_hash:
        raise PromptRegistryError(
            f"Immutable prompt hash mismatch for {name}@{version}: "
            f"expected {expected_hash}, got {actual_hash}"
        )

    data = _read_yaml(path)
    if data.get("name") != name or str(data.get("version")) != version:
        raise PromptRegistryError(
            f"Prompt identity mismatch in {path}: "
            f"{data.get('name')}@{data.get('version')}"
        )
    sections = data.get("sections") or {}
    if not isinstance(sections, dict):
        raise PromptRegistryError(f"Prompt sections must be a mapping: {path}")
    return PromptTemplate(
        name=name,
        version=version,
        system_template=str(data.get("system") or ""),
        user_template=str(data.get("user") or ""),
        template_hash=actual_hash,
        source_path=str(path),
        sections=MappingProxyType(
            {str(key): str(value) for key, value in sections.items()}
        ),
    )


def get_prompt(name: str, version: str | None = None) -> PromptTemplate:
    """获取显式版本；未指定时解析当前活动版本。"""
    selected_version = version
    if selected_version is None:
        try:
            selected_version = get_active_versions()[name]
        except KeyError as exc:
            raise KeyError(name) from exc
    return _load_prompt(name, selected_version)


def activate_prompt_version(name: str, version: str) -> PromptTemplate:
    """先完整验证目标版本，再原子替换活动版本映射文件。"""
    prompt = get_prompt(name, version=version)
    with _ACTIVATION_LOCK:
        active = get_active_versions()
        active[name] = version
        payload = yaml.safe_dump(
            {"active_versions": active},
            allow_unicode=True,
            sort_keys=True,
        )
        temporary_path = ACTIVE_VERSIONS_PATH.with_suffix(".yaml.tmp")
        temporary_path.write_text(payload, encoding="utf-8")
        os.replace(temporary_path, ACTIVE_VERSIONS_PATH)
    return prompt


def prompt_trace(
    name: str,
    model: str,
    retrieval_top_k: int,
    fallback: bool,
    *,
    prompt: PromptTemplate | None = None,
) -> dict:
    """生成随 Agent 结果保存的模板版本、哈希、模型与检索追踪字段。"""
    actual_prompt = prompt or get_prompt(name)
    if actual_prompt.name != name:
        raise PromptRegistryError(
            f"Trace prompt mismatch: expected {name}, got {actual_prompt.name}"
        )
    return {
        "template_name": actual_prompt.name,
        "template_version": actual_prompt.version,
        "template_hash": actual_prompt.template_hash,
        "model": model,
        "retrieval_top_k": retrieval_top_k,
        "fallback": fallback,
    }

