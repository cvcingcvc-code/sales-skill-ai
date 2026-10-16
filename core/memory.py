"""学员记忆模块 — SQLite 存储学员进度、会话记录和能力画像。

表结构:
  learners: id, name, company, created_at
  sessions: id, learner_id, scenario_id, score_json, transcript_json, created_at
  skills:   learner_id, dimension, score (原始分), metadata_json, updated_at
"""

import os
import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Optional


DB_PATH = os.getenv("DB_PATH", "./data/db/learners.db")


def _ensure_db_dir():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表（首次运行时调用）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS learners (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            company     TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            learner_id    TEXT NOT NULL,
            scenario_id   TEXT NOT NULL,
            score_json    TEXT DEFAULT '{}',
            transcript_json TEXT DEFAULT '[]',
            created_at    TEXT NOT NULL,
            FOREIGN KEY (learner_id) REFERENCES learners(id)
        );
        CREATE TABLE IF NOT EXISTS skills (
            learner_id  TEXT NOT NULL,
            dimension   TEXT NOT NULL,
            score       REAL NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (learner_id, dimension),
            FOREIGN KEY (learner_id) REFERENCES learners(id)
        );
    """)
    # 增量迁移：原有分数及会话原文保持不变；不能凭分数大小猜测历史量纲。
    conn.execute("BEGIN IMMEDIATE")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(skills)")}
    if "metadata_json" not in columns:
        conn.execute("ALTER TABLE skills ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 学员管理
# ---------------------------------------------------------------------------

def create_learner(learner_id: str, name: str, company: str = "") -> dict:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO learners (id, name, company, created_at) VALUES (?, ?, ?, ?)",
        (learner_id, name, company, now),
    )
    conn.commit()
    conn.close()
    return {"id": learner_id, "name": name, "company": company}


def get_learner(learner_id: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM learners WHERE id = ?", (learner_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_learners() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM learners ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 会话记录
# ---------------------------------------------------------------------------

def score_scale_info(score_result: dict) -> dict:
    """读取显式评分量纲；缺失元数据的旧分数只保留原值，绝不自动乘五。"""
    scale = score_result.get("scale")
    status = "legacy_unverified" if "scale" not in score_result else "invalid_scale"
    dimension_max = None
    total_max = None
    if isinstance(scale, dict):
        candidate = scale.get("dimension_max")
        total = scale.get("total_max", 100)
        if (type(candidate) in {int, float} and candidate in {20, 100}
                and type(total) in {int, float} and total == 100):
            dimension_max, total_max, status = candidate, total, "explicit"
    return {
        "dimension_max": dimension_max,
        "total_max": total_max,
        "scale_status": status,
        "score_source": score_result.get("score_source", "legacy_unverified"),
        "score_status": score_result.get("score_status", "unverified"),
    }


def _dimension_metadata(score_result: dict, score: float) -> dict:
    metadata = score_scale_info(score_result)
    maximum = metadata["dimension_max"]
    if maximum is not None and not 0 <= score <= maximum:
        metadata["scale_status"] = "invalid_scale"
    return metadata


def save_session(
    learner_id: str,
    scenario_id: str,
    score_result: dict,
    transcript: list[dict],
    session_id: Optional[str] = None,
) -> str:
    dimensions = score_result.get("dimensions", {})
    if not isinstance(dimensions, dict) or any(
        type(value) not in {int, float} or not math.isfinite(value)
        for value in dimensions.values()
    ):
        raise ValueError("维度分必须为有限数值")
    conn = _get_conn()
    import uuid
    sid = session_id or str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO sessions (id, learner_id, scenario_id, score_json, transcript_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, learner_id, scenario_id, json.dumps(score_result, ensure_ascii=False),
         json.dumps(transcript, ensure_ascii=False), now),
    )
    # 更新技能维度
    for dim, score in dimensions.items():
        metadata = _dimension_metadata(score_result, score)
        conn.execute(
            "INSERT INTO skills (learner_id, dimension, score, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(learner_id, dimension) DO UPDATE SET score = excluded.score, "
            "updated_at = excluded.updated_at, metadata_json = excluded.metadata_json",
            (learner_id, dim, score, now, json.dumps(metadata, ensure_ascii=False)),
        )
    conn.commit()
    conn.close()
    return sid


def get_learner_sessions(learner_id: str, limit: int = 20) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions WHERE learner_id = ? ORDER BY created_at DESC LIMIT ?",
        (learner_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 能力画像
# ---------------------------------------------------------------------------

def get_learner_progress(learner_id: str) -> dict:
    """显式量纲转换为百分制；历史量纲不明时兼容原值并标记来源。"""
    conn = _get_conn()

    skill_rows = conn.execute(
        "SELECT dimension, score, updated_at, metadata_json FROM skills WHERE learner_id = ? ORDER BY dimension",
        (learner_id,),
    ).fetchall()
    skills, raw_dimensions, normalized_dimensions, provenance = {}, {}, {}, {}
    for row in skill_rows:
        dim, raw_score = row["dimension"], row["score"]
        metadata = json.loads(row["metadata_json"] or "{}")
        if not metadata:
            metadata = score_scale_info({})
        raw_dimensions[dim] = raw_score
        provenance[dim] = metadata
        if metadata.get("scale_status") == "explicit":
            normalized_dimensions[dim] = round(raw_score / metadata["dimension_max"] * 100, 6)
        skills[dim] = normalized_dimensions.get(dim, raw_score)

    session_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM sessions WHERE learner_id = ?", (learner_id,)
    ).fetchone()["cnt"]

    # 最近 5 次会话评分
    recent = conn.execute(
        "SELECT score_json, created_at FROM sessions WHERE learner_id = ? ORDER BY created_at DESC LIMIT 5",
        (learner_id,),
    ).fetchall()

    conn.close()

    recent_scores = []
    for r in recent:
        sj = json.loads(r["score_json"])
        recent_scores.append({
            "total_score": sj.get("total_score", 0),
            "date": r["created_at"][:10],
            "provenance": score_scale_info(sj),
        })

    return {
        "learner_id": learner_id,
        "dimensions": skills,
        "raw_dimensions": raw_dimensions,
        "normalized_dimensions": normalized_dimensions,
        "scale": {
            "display_dimension_max": 100,
            "raw_dimension_max": {dim: info.get("dimension_max") for dim, info in provenance.items()},
        },
        "provenance": provenance,
        "total_sessions": session_count,
        "recent_scores": recent_scores,
    }


def get_weak_areas(learner_id: str, threshold: float = 70.0) -> list[str]:
    """百分制阈值。模糊的历史 0–20 分不参与判断，旧百分制记录兼容原值。"""
    progress = get_learner_progress(learner_id)
    dims = progress["dimensions"]
    weak = []
    for dim, score in dims.items():
        status = progress["provenance"][dim]["scale_status"]
        if status == "invalid_scale":
            continue
        if status == "legacy_unverified" and not 20 < score <= 100:
            continue
        if score < threshold:
            weak.append(dim)
    return weak


def get_recommended_scenarios(learner_id: str) -> list[str]:
    """基于弱点推荐练习场景 ID。"""
    weak = get_weak_areas(learner_id)
    if not weak:
        # 无弱点时，推荐最近最少练的维度对应场景
        return []

    # 维度→场景映射
    dimension_scenario_map = {
        "开场破冰": ["cold_visit_001"],
        "需求挖掘": ["cold_visit_001", "follow_up_001"],
        "价值陈述": ["objection_001", "cold_visit_001"],
        "异议处理": ["objection_001"],
        "推进下一步": ["follow_up_001", "objection_001"],
        "信任重建": ["follow_up_001"],
        "需求唤醒": ["follow_up_001"],
        "专业展示": ["follow_up_001", "cold_visit_001"],
        "异议化解": ["objection_001"],
        "推进约见": ["follow_up_001", "cold_visit_001"],
        "价格谈判心态": ["objection_001"],
        "价值再传递": ["objection_001"],
        "竞品对比处理": ["objection_001"],
        "风险化解": ["objection_001"],
        "开场": ["cold_visit_001"],
    }

    recommended = set()
    for w in weak:
        scenarios = dimension_scenario_map.get(w, [])
        recommended.update(scenarios)

    return list(recommended)
