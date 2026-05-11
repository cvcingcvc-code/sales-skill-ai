"""场景加载器 — 从 YAML 模板加载训练场景。"""

import yaml
from pathlib import Path
from typing import Optional

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_all() -> list[dict]:
    """加载所有 YAML 场景文件。"""
    scenarios = []
    for filepath in sorted(_TEMPLATES_DIR.glob("*.yaml")):
        with open(filepath, "r", encoding="utf-8") as f:
            scenario = yaml.safe_load(f)
            scenario["_filename"] = filepath.stem
            scenarios.append(scenario)
    return scenarios


def load_scenario(scenario_id: str) -> Optional[dict]:
    """根据 scenario_id（YAML 内部 id 字段）加载单个场景。"""
    for s in _load_all():
        if s.get("id") == scenario_id:
            return s
    # fallback: 按文件名匹配
    filepath = _TEMPLATES_DIR / f"{scenario_id}.yaml"
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return None


def list_scenarios(difficulty: Optional[str] = None) -> list[dict]:
    """列出所有可用场景，可按难度筛选。"""
    scenarios = _load_all()
    if difficulty:
        scenarios = [s for s in scenarios if s.get("difficulty") == difficulty]
    return scenarios


def get_difficulty_levels() -> list[str]:
    """返回所有难度级别。"""
    levels = set()
    for s in _load_all():
        if "difficulty" in s:
            levels.add(s["difficulty"])
    return sorted(levels)
