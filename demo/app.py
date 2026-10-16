"""
Streamlit 演示 UI — 销售神队友可视化界面。

启动: streamlit run demo/app.py
"""

import sys
import os
import hmac

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import streamlit as st
from core.skill_engine import SkillEngine
from core import memory
from demo.support_admin import render_support_admin

st.set_page_config(page_title="AI 销售与客服工作台", page_icon="🎯", layout="wide")

# This Streamlit application is an administrative workbench. Server-side API
# credentials never implicitly authenticate a visitor/browser session.
expected_admin_token = os.environ.get("ADMIN_API_TOKEN", "")
browser_admin_token = st.sidebar.text_input("管理员访问令牌", type="password", key="workbench_admin_token")
if not expected_admin_token:
    st.error("BLOCKED：请先在服务端环境设置 ADMIN_API_TOKEN，再登录管理工作台。")
    st.stop()
if not browser_admin_token:
    st.info("请输入管理员访问令牌以登录销售训练与客服管理工作台。")
    st.stop()
if not hmac.compare_digest(browser_admin_token.encode("utf-8"), expected_admin_token.encode("utf-8")):
    st.error("BLOCKED：管理员访问未获授权。")
    st.stop()

workspace_mode = st.sidebar.radio("工作区", ["销售训练", "客服工作台"], key="workspace_mode")
if workspace_mode == "客服工作台":
    render_support_admin(admin_token=browser_admin_token)
    st.stop()


def show_training_error():
    # Provider exceptions may contain transport details; never display raw exceptions.
    st.error("BLOCKED：模型调用未完成。请检查模型配置、密钥和网络后重试；本次未生成模型结果。")


def show_score(result):
    st.divider()
    st.subheader("📊 评分结果")
    source = getattr(result, "score_source", "unknown")
    status = getattr(result, "score_status", "unknown")
    source_name = {"model": "模型评分", "rule": "规则评分", "rules": "规则评分"}.get(source, "未标注来源")
    st.caption(f"评分来源：{source_name} · 状态：{status}")
    st.caption("维度原始分为 0–20 分；能力雷达按原始分 × 5 换算为 0–100 分。总分单独进行难度校准。")
    if source in {"rule", "rules"}:
        st.warning("当前为规则评分；规则结果不能作为真实模型评分验收通过的依据。")
    if status in {"blocked", "BLOCKED", "error", "failed"}:
        st.warning("BLOCKED：模型评分不可用。下面仅展示已有的明确来源结果。")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric("总分", f"{result.total_score}/100")
    with col2:
        if result.dimensions:
            import pandas as pd
            st.bar_chart(pd.DataFrame({"维度": list(result.dimensions), "得分": list(result.dimensions.values())}).set_index("维度"))
    if result.strengths:
        st.success("✅ 亮点: " + " | ".join(result.strengths))
    if result.weaknesses:
        st.warning("⚠️ 需改进: " + " | ".join(result.weaknesses))
    if result.suggestions:
        st.subheader("💡 改进建议")
        for suggestion in result.suggestions[:3]:
            with st.expander(suggestion.get("issue", "建议")[:60] + "..."):
                st.write("问题：", suggestion.get("issue", ""))
                st.write("建议：", suggestion.get("better_approach", ""))
                st.write(suggestion.get("example", ""))

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

if "engine" not in st.session_state:
    st.session_state.engine = SkillEngine()
if "learner_id" not in st.session_state:
    st.session_state.learner_id = "demo_user"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "in_session" not in st.session_state:
    st.session_state.in_session = False

engine = st.session_state.engine
memory.init_db()

# 确保默认学员存在
memory.create_learner(st.session_state.learner_id, "演示用户", "Demo公司")

# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🎯 销售神队友")
    st.caption("企业话术训练 AI v1.0")

    # 学员管理
    learners = memory.list_learners()
    learner_names = [l["id"] for l in learners] or ["demo_user"]
    selected = st.selectbox("选择学员", learner_names,
                            index=learner_names.index(st.session_state.learner_id)
                            if st.session_state.learner_id in learner_names else 0)
    if selected != st.session_state.learner_id:
        st.session_state.learner_id = selected
        st.session_state.chat_history = []
        st.session_state.in_session = False
        st.session_state.pop("last_score", None)
        st.rerun()

    # 新建学员
    with st.expander("+ 新建学员"):
        new_name = st.text_input("姓名")
        new_company = st.text_input("公司")
        if st.button("创建") and new_name:
            lid = new_name.lower().replace(" ", "_")
            memory.create_learner(lid, new_name, new_company)
            st.session_state.learner_id = lid
            st.session_state.chat_history = []
            st.session_state.in_session = False
            st.session_state.pop("last_score", None)
            st.rerun()

    st.divider()

    # 进度概览
    report = engine.get_progress_report(st.session_state.learner_id)
    progress = report.get("progress", {})
    st.metric("训练次数", progress.get("total_sessions", 0))

    dims = progress.get("normalized_dimensions", {})
    if dims:
        st.subheader("能力雷达")
        import pandas as pd
        import plotly.express as px

        df = pd.DataFrame({
            "维度": list(dims.keys()),
            "得分": list(dims.values()),
        })
        fig = px.line_polar(df, r="得分", theta="维度", line_close=True, range_r=[0, 100])
        fig.update_traces(fill="toself")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("量纲：0–100 分；只展示有明确原始量纲的维度。薄弱阈值为 70 分。")
    unverified = {
        dim: progress.get("raw_dimensions", {}).get(dim)
        for dim, info in progress.get("provenance", {}).items()
        if info.get("scale_status") != "explicit"
    }
    if unverified:
        st.info("历史或异常记录的量纲未验证（legacy_unverified / invalid_scale），保留原值且不绘入雷达；历史 0–20 分不自动放大，也不据此推荐弱项。")
        st.write("未验证维度原始分：", unverified)

    st.divider()
    st.caption("Powered by DeepSeek")

# ---------------------------------------------------------------------------
# 主区域 — 标签页
# ---------------------------------------------------------------------------

tab1, tab2, tab3 = st.tabs(["⚔️ 实战演练", "📖 话术学习", "📊 详细报告"])

# ---- 实战演练 ----
with tab1:
    st.header("场景模拟对话")

    # 场景选择（仅在未开始对话时显示）
    if not st.session_state.in_session:
        col1, col2 = st.columns(2)
        with col1:
            diff = st.selectbox("难度筛选", ["全部", "beginner", "intermediate", "hard"])
        scenarios = engine.list_available_scenarios(None if diff == "全部" else diff)
        scenario_options = {s["name"]: s for s in scenarios}
        if not scenario_options:
            st.info("当前难度暂无可用场景，请选择其他难度。")
        else:
            with col2:
                selected_name = st.selectbox("选择场景", list(scenario_options))
            scenario = scenario_options[selected_name]
            profile = scenario.get("customer_profile", {})
            with st.expander("场景详情"):
                st.write("目标：", scenario.get("sales_objective", ""))
                st.write("客户：", profile.get("name", ""), profile.get("occupation", ""))
                st.write("性格：", profile.get("personality", ""))
                st.write("初始态度：", profile.get("initial_attitude", ""))
                st.write("顾虑：", "、".join(profile.get("pain_points", [])))
            if st.button("开始对话", type="primary", use_container_width=True):
                try:
                    opening = engine.start_roleplay(scenario["id"], st.session_state.learner_id)
                except Exception:
                    show_training_error()
                else:
                    st.session_state.chat_history = [{"role": "customer", "content": opening}]
                    st.session_state.pop("last_score", None)
                    st.session_state.in_session = True
                    st.rerun()

    # 对话进行中
    else:
        chat_container = st.container()
        with chat_container:
            for msg in st.session_state.chat_history:
                if msg["role"] == "customer":
                    with st.chat_message("assistant"):
                        st.write(msg["content"])
                else:
                    with st.chat_message("user"):
                        st.write(msg["content"])

        # 输入框
        user_input = st.chat_input("输入你的话术...", key="chat_input")

        if user_input:
            try:
                reply, done = engine.respond(user_input)
            except Exception:
                show_training_error()
            else:
                st.session_state.chat_history.extend([
                    {"role": "sales", "content": user_input},
                    {"role": "customer", "content": reply},
                ])
                if done or engine.current_round >= 14:
                    try:
                        st.session_state.last_score = engine.finish_session()
                    except Exception:
                        show_training_error()
                    else:
                        st.session_state.in_session = False
                        st.rerun()
                else:
                    st.rerun()

        if st.button("结束对话（评分）"):
            try:
                st.session_state.last_score = engine.force_end_session()
            except Exception:
                show_training_error()
            else:
                st.session_state.in_session = False
                st.rerun()

    if "last_score" in st.session_state and not st.session_state.in_session:
        show_score(st.session_state.last_score)
        if st.button("再来一局", type="primary"):
            st.session_state.chat_history = []
            del st.session_state.last_score
            st.rerun()

# ---- 话术学习 ----
with tab2:
    st.header("话术知识库")

    category = st.radio("类别", ["异议处理", "产品介绍", "成交跟进"], horizontal=True)
    cat_key = {"异议处理": "objections", "产品介绍": "product_pitch", "成交跟进": "closing"}
    scripts = engine.get_scripts_by_category(cat_key[category])

    for s in scripts:
        with st.expander(f"{s.get('id')} — {s.get('scenario', s.get('context', ''))[:50]}", expanded=False):
            if "trigger" in s:
                st.caption(f"触发词: {' / '.join(s['trigger'])}")
            if "context" in s:
                st.caption(f"场景: {s['context']}")
            st.markdown(f"**话术**: {s.get('standard_response', '')}")
            st.caption(f"要点: {' · '.join(s.get('key_points', []))}")
            st.caption(f"避免: {' · '.join(s.get('avoid', []))}")
            st.caption(f"难度: {s.get('difficulty', '')}")

    if st.button("AI 出题测验"):
        quiz = []
        try:
            with st.spinner("AI 出题中..."):
                quiz = engine.generate_quiz(cat_key[category], 3)
        except Exception:
            show_training_error()
        for i, q in enumerate(quiz, 1):
            st.subheader(f"第{i}题")
            st.write(q.get("question", ""))
            options = q.get("options", [])
            if options:
                answer = st.radio(f"选择答案", options, key=f"quiz_{i}", index=None)
                if answer and answer.startswith(q.get("correct", "")):
                    st.success(f"✅ 正确! {q.get('explanation', '')}")
                elif answer:
                    st.error(f"❌ 正确答案是 {q.get('correct', '')}. {q.get('explanation', '')}")

# ---- 详细报告 ----
with tab3:
    st.header("学员进度报告")

    sessions = memory.get_learner_sessions(st.session_state.learner_id, limit=20)
    if not sessions:
        st.info("暂无训练记录")
    else:
        import json
        import pandas as pd

        records = []
        for s in sessions:
            sj = json.loads(s["score_json"])
            records.append({
                "日期": s["created_at"][:10],
                "场景": s["scenario_id"],
                "总分": sj.get("total_score", 0),
                "评分来源": sj.get("score_source", "未标注（历史记录）"),
                "评分状态": sj.get("score_status", "未标注（历史记录）"),
                "维度满分": memory.score_scale_info(sj)["dimension_max"] or "量纲未验证",
            })
        df = pd.DataFrame(records)
        st.line_chart(df.set_index("日期")["总分"])

        st.subheader("历史记录")
        for s in sessions:
            sj = json.loads(s["score_json"])
            with st.expander(f"{s['created_at'][:10]} — {s['scenario_id']} — {sj.get('total_score', '?')}分"):
                st.json(sj)
