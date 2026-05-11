"""
CLI 主入口 — 「销售神队友 · 企业话术训练 AI」

用法: python -m cli.main
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core.skill_engine import SkillEngine
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress
from rich import box

console = Console()


def show_banner():
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]销售神队友 · 企业话术训练 AI[/bold cyan]  [dim]v1.0[/dim]\n"
            "[dim]Powered by DeepSeek[/dim]",
            box=box.DOUBLE,
        )
    )


def show_main_menu() -> str:
    console.print("\n[bold]主菜单[/bold]")
    console.print("  [1] 新员工入门 — 学习话术库")
    console.print("  [2] 实战演练 — 场景模拟对话")
    console.print("  [3] 查看我的进度报告")
    console.print("  [4] 选择学员")
    console.print("  [q] 退出")
    return Prompt.ask("\n请选择", choices=["1", "2", "3", "4", "q"], default="1")


# ---------------------------------------------------------------------------
# 新员工模式
# ---------------------------------------------------------------------------

def new_employee_mode(engine: SkillEngine, learner_id: str):
    console.print("\n[bold cyan]📖 新员工入门 — 话术学习[/bold cyan]")

    category = Prompt.ask(
        "选择话术类别",
        choices=["objections", "product_pitch", "closing"],
        default="objections",
    )
    labels = {"objections": "异议处理", "product_pitch": "产品介绍", "closing": "成交跟进"}
    scripts = engine.get_scripts_by_category(category)

    console.print(f"\n[bold]{labels[category]} 话术库[/bold] — 共 {len(scripts)} 条\n")

    for i, s in enumerate(scripts, 1):
        console.print(f"[cyan]#{i}[/cyan] [bold]{s.get('id')}[/bold]")
        if "scenario" in s:
            console.print(f"  场景: {s['scenario']}")
        if "context" in s:
            console.print(f"  背景: {s['context']}")
        console.print(f"  话术: [green]{s.get('standard_response', '')}[/green]")
        console.print(f"  要点: [dim]{', '.join(s.get('key_points', []))}[/dim]")
        console.print(f"  难度: {s.get('difficulty', 'unknown')}")
        console.print()

        if i % 3 == 0 and i < len(scripts):
            if not Confirm.ask("继续浏览?", default=True):
                break

    # AI 测验
    if Confirm.ask("\n要做一个知识测验吗?", default=True):
        console.print("\n[bold yellow]📝 AI 出题中...[/bold yellow]")
        quiz = engine.generate_quiz(category, count=3)
        score = 0
        for i, q in enumerate(quiz, 1):
            console.print(f"\n[bold]第{i}题:[/bold] {q.get('question', '')}")
            options = q.get("options", [])
            for opt in options:
                console.print(f"  {opt}")
            answer = Prompt.ask("你的答案", choices=[o[0] for o in options if o], default=options[0][0] if options else "A")
            correct = q.get("correct", "")
            if answer.upper() == correct.upper():
                console.print("[green]✅ 正确![/green]")
                score += 1
            else:
                console.print(f"[red]❌ 正确答案是 {correct}[/red]")
            console.print(f"[dim]{q.get('explanation', '')}[/dim]")
        console.print(f"\n[bold]测验得分: {score}/{len(quiz)}[/bold]")


# ---------------------------------------------------------------------------
# 实战演练模式
# ---------------------------------------------------------------------------

def battle_mode(engine: SkillEngine, learner_id: str):
    console.print("\n[bold cyan]⚔️ 实战演练 — 场景模拟对话[/bold cyan]")

    # 选择难度
    difficulty = Prompt.ask(
        "选择难度",
        choices=["beginner", "intermediate", "hard", "all"],
        default="beginner",
    )
    diff_filter = None if difficulty == "all" else difficulty
    scenarios = engine.list_available_scenarios(diff_filter)

    if not scenarios:
        console.print("[red]没有找到匹配的场景[/red]")
        return

    # 显示场景列表
    console.print()
    table = Table(title="可用场景")
    table.add_column("#", style="cyan")
    table.add_column("场景名")
    table.add_column("难度")
    table.add_column("目标")

    for i, s in enumerate(scenarios, 1):
        table.add_row(
            str(i),
            s.get("name", "未知"),
            s.get("difficulty", ""),
            s.get("sales_objective", "")[:30],
        )

    console.print(table)

    choice = Prompt.ask("选择场景编号", default="1")
    try:
        idx = int(choice) - 1
        scenario = scenarios[idx]
    except (ValueError, IndexError):
        console.print("[red]无效选择[/red]")
        return

    scenario_id = scenario["id"]
    customer_name = scenario.get("customer_profile", {}).get("name", "客户")

    console.print(f"\n[bold]场景: {scenario['name']}[/bold]")
    console.print(f"目标: {scenario.get('sales_objective', '')}")
    console.print(f"客户: {customer_name} | 态度: {scenario.get('customer_profile', {}).get('initial_attitude', '')}")
    console.print(f"\n[dim]输入 Ctrl+C 结束对话[/dim]\n")

    # 开始对话
    try:
        opening = engine.start_roleplay(scenario_id, learner_id)
        console.print(f"[yellow][客户-{customer_name}]:[/yellow] {opening}")

        while engine.is_in_session and engine.current_round < 15:
            user_input = Prompt.ask(f"\n[cyan][你][/cyan]")
            if not user_input.strip():
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break

            with Progress() as progress:
                task = progress.add_task("[cyan]客户思考中...", total=None)
                reply, done = engine.respond(user_input)
                progress.remove_task(task)

            console.print(f"[yellow][客户-{customer_name}]:[/yellow] {reply}")

        # 评分
        console.print("\n[bold]对话结束，正在评分...[/bold]")
        result = engine.finish_session()
        show_score(result)

    except KeyboardInterrupt:
        console.print("\n[dim]对话提前结束[/dim]")
        try:
            result = engine.force_end_session()
            show_score(result)
        except Exception:
            pass


def show_score(result):
    """展示评分结果。"""
    console.print()
    table = Table(title="📊 话术评分报告", box=box.ROUNDED)
    table.add_column("维度", style="cyan")
    table.add_column("得分", style="yellow")
    table.add_column("满分", style="dim")

    for dim, score in result.dimensions.items():
        bar = "█" * (score // 5) + "░" * (20 - score // 5)
        table.add_row(f"{dim}\n[dim]{bar}[/dim]", str(score), "20")

    console.print(table)
    console.print(f"\n[bold]总分: {result.total_score}/100[/bold]")

    if result.strengths:
        console.print("\n[green]✅ 亮点:[/green]")
        for s in result.strengths:
            console.print(f"  + {s}")

    if result.weaknesses:
        console.print("\n[red]⚠️ 需改进:[/red]")
        for w in result.weaknesses:
            console.print(f"  - {w}")

    if result.suggestions:
        console.print("\n[bold]💡 改进建议:[/bold]")
        for i, s in enumerate(result.suggestions[:3], 1):
            console.print(f"\n  {i}. [yellow]问题:[/yellow] {s.get('issue', '')}")
            console.print(f"     [green]建议:[/green] {s.get('better_approach', '')}")
            console.print(f"     [dim]示范: {s.get('example', '')}[/dim]")

    if result.recommended_practice:
        console.print(f"\n[bold]📚 推荐练习话术:[/bold] {', '.join(result.recommended_practice)}")


# ---------------------------------------------------------------------------
# 进度报告
# ---------------------------------------------------------------------------

def progress_report(engine: SkillEngine, learner_id: str):
    console.print(f"\n[bold cyan]📊 学员进度报告 — {learner_id}[/bold cyan]\n")

    report = engine.get_progress_report(learner_id)
    progress = report.get("progress", {})

    if not progress.get("total_sessions"):
        console.print("[dim]暂无训练记录，去实战演练中完成一次对话吧！[/dim]")
        return

    console.print(f"总训练次数: [bold]{progress.get('total_sessions', 0)}[/bold]")

    dims = progress.get("dimensions", {})
    if dims:
        console.print("\n[bold]能力维度:[/bold]")
        for dim, score in sorted(dims.items()):
            bar = "█" * (int(score) // 5) + "░" * (20 - int(score) // 5)
            console.print(f"  {dim:12s} {bar} {score:.0f}")

    recent = progress.get("recent_scores", [])
    if recent:
        console.print("\n[bold]最近训练:[/bold]")
        for r in recent:
            console.print(f"  {r['date']} — {r['total_score']}分")

    weak = report.get("weak_areas", [])
    if weak:
        console.print(f"\n[yellow]薄弱维度: {', '.join(weak)}[/yellow]")

    rec = report.get("recommended_scenarios", [])
    if rec:
        console.print(f"[cyan]推荐场景: {', '.join(rec)}[/cyan]")


# ---------------------------------------------------------------------------
# 学员管理
# ---------------------------------------------------------------------------

_current_learner_id = "default_learner"


def select_learner(engine: SkillEngine):
    global _current_learner_id
    from core import memory

    learners = memory.list_learners()

    console.print("\n[bold]学员管理[/bold]")
    if learners:
        table = Table(title="已有学员")
        table.add_column("#")
        table.add_column("姓名")
        table.add_column("公司")
        table.add_column("创建时间")
        for i, l in enumerate(learners, 1):
            table.add_row(str(i), l.get("name", ""), l.get("company", ""), l.get("created_at", "")[:10])
        console.print(table)

    console.print(f"\n当前学员: [bold cyan]{_current_learner_id}[/bold cyan]")
    action = Prompt.ask("操作", choices=["switch", "new", "back"], default="back")

    if action == "new":
        name = Prompt.ask("学员姓名")
        company = Prompt.ask("公司(可选)", default="")
        lid = name.lower().replace(" ", "_")
        memory.create_learner(lid, name, company)
        _current_learner_id = lid
        console.print(f"[green]创建成功! 当前学员: {_current_learner_id}[/green]")
    elif action == "switch" and learners:
        idx = Prompt.ask("选择编号", default="1")
        try:
            _current_learner_id = learners[int(idx) - 1]["id"]
            console.print(f"[green]已切换到: {_current_learner_id}[/green]")
        except (ValueError, IndexError):
            console.print("[red]无效选择[/red]")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    engine = SkillEngine()
    show_banner()

    while True:
        choice = show_main_menu()

        if choice == "q":
            console.print("\n[dim]再见！祝销售顺利！[/dim]\n")
            break
        elif choice == "1":
            new_employee_mode(engine, _current_learner_id)
        elif choice == "2":
            battle_mode(engine, _current_learner_id)
        elif choice == "3":
            progress_report(engine, _current_learner_id)
        elif choice == "4":
            select_learner(engine)


if __name__ == "__main__":
    main()
