# Skills 系统 —— 对应 hiclaw 的 SKILL.md + scripts/ + references/
#
# Skill = 面向 Agent 的 Markdown（SKILL.md，带 frontmatter）+ 可选脚本/参考。
# SkillLoader 从目录扫描 SKILL.md；materialize 将选中 skills 物化进 Worker
# workspace（agents/<name>/skills/<skill>/SKILL.md）。
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .object_store import ObjectStore


@dataclass
class Skill:
    name: str
    description: str
    content: str                       # SKILL.md 正文（去 frontmatter）
    raw: str                           # 原始全文

    def render(self) -> str:
        return self.raw


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """简易 YAML frontmatter 解析（仅 key: value 行），避免引入依赖。"""
    meta: Dict[str, str] = {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return meta, text
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


class SkillLoader:
    """从文件系统目录扫描 skills。

    目录结构：<root>/<skill-name>/SKILL.md [ + scripts/ + references/ ]
    """

    def __init__(self, root: str):
        self.root = root

    def list_names(self) -> List[str]:
        if not os.path.isdir(self.root):
            return []
        return sorted(
            d for d in os.listdir(self.root)
            if os.path.isdir(os.path.join(self.root, d))
            and os.path.isfile(os.path.join(self.root, d, "SKILL.md"))
        )

    def load(self, name: str) -> Optional[Skill]:
        path = os.path.join(self.root, name, "SKILL.md")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        meta, body = _parse_frontmatter(raw)
        return Skill(
            name=name,
            description=meta.get("description", ""),
            content=body.strip(),
            raw=raw,
        )

    def load_all(self) -> Dict[str, Skill]:
        return {n: s for n in self.list_names() if (s := self.load(n))}


def materialize_skills(store: ObjectStore, worker_name: str,
                       skills: List[Skill]) -> None:
    """将 skills 物化进 Worker workspace：agents/<name>/skills/<skill>/SKILL.md。"""
    for sk in skills:
        store.put(f"agents/{worker_name}/skills/{sk.name}/SKILL.md", sk.raw)
