"""CLI 效果演示 — 模拟完整训练流程，展示所有界面。"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

# ======================================================================
# BANNER
# ======================================================================
console.print()
console.print(Panel.fit(
    "[bold cyan]销售神队友 · 企业话术训练 AI[/bold cyan]  [dim]v1.0[/dim]\n"
    "[dim]Powered by DeepSeek[/dim]",
    box=box.DOUBLE,
))

# ======================================================================
# 场景列表
# ======================================================================
from core.skill_engine import SkillEngine
engine = SkillEngine()

console.print("\n[bold]⚔️ 实战演练 — 可用场景[/bold]")
table = Table()
table.add_column("#", style="cyan")
table.add_column("场景名")
table.add_column("难度")
table.add_column("销售目标")
for i, s in enumerate(engine.list_available_scenarios(), 1):
    diff_color = {"beginner": "green", "intermediate": "yellow", "hard": "red"}
    color = diff_color.get(s.get("difficulty", ""), "white")
    table.add_row(str(i), s["name"], f"[{color}]{s['difficulty']}[/{color}]",
                  s.get("sales_objective", "")[:50])
console.print(table)

# ======================================================================
# 角色扮演对话
# ======================================================================
console.print("\n[bold cyan]▶ 开始场景: 陌拜·企业软件获客 (beginner)[/bold cyan]")
console.print("[dim]目标: 获得下次详谈机会 · 客户态度: cold · 最多15轮[/dim]")
console.print("[dim]──────────────────────────────────────────────[/dim]\n")

opening = engine.start_roleplay("cold_visit_001", "demo_user")
console.print(f"[bold yellow][客户-王先生][/bold yellow] {opening}\n")

# Round 1
r1 = "王总您好，冒昧打扰。我是XX公司的销售顾问，专门帮B2B销售团队解决新人上手慢的问题。方便聊两分钟吗？"
console.print(f"[bold cyan][你][/bold cyan] {r1}")
reply1, done1 = engine.respond(r1)
console.print(f"[bold yellow][客户-王先生][/bold yellow] {reply1}\n")

# Round 2
r2 = "理解理解，您时间宝贵。我不绕弯子——我们刚帮一家和贵司规模差不多的公司，把销售新人从入职到独立出单的时间从3个月压缩到了2周。如果您对这个数字有兴趣，我可以花5分钟具体说说怎么做到的。"
console.print(f"[bold cyan][你][/bold cyan] {r2}")
reply2, done2 = engine.respond(r2)
console.print(f"[bold yellow][客户-王先生][/bold yellow] {reply2}\n")

# Round 3
r3 = "具体做法是：新人入职后，系统自动推荐最适合当前客户的话术，不用自己琢磨。同时主管可以看到每个人的薄弱环节，针对性辅导。那家公司在试用一个月后新人首月成交率涨了28%。要不我帮您开个免费试用账号，您选两个新人先试试效果？"
console.print(f"[bold cyan][你][/bold cyan] {r3}")
reply3, done3 = engine.respond(r3)
console.print(f"[bold yellow][客户-王先生][/bold yellow] {reply3}\n")

# Score
result = engine.finish_session()

# ======================================================================
# 评分报告
# ======================================================================
console.print("[dim]──────────────────────────────────────────────[/dim]")
console.print(Panel.fit("[bold]📊 话术评分报告[/bold]", box=box.ROUNDED))

score_table = Table(box=box.ROUNDED)
score_table.add_column("评分维度", style="cyan")
score_table.add_column("得分", style="yellow", justify="right")
score_table.add_column("满分", style="dim", justify="right")
score_table.add_column("可视化")
for dim, score in result.dimensions.items():
    pct = score / 20 * 100
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    score_table.add_row(dim, str(score), "20", f"[dim]{bar}[/dim]")
console.print(score_table)

console.print(f"\n  [bold]总分: {result.total_score}/100[/bold]")

if result.strengths:
    console.print("\n[bold green]  ✅ 亮点[/bold green]")
    for s in result.strengths[:3]:
        console.print(f"    • {s}")

if result.weaknesses:
    console.print("\n[bold red]  ⚠️ 需改进[/bold red]")
    for w in result.weaknesses[:3]:
        console.print(f"    • {w}")

if result.suggestions:
    console.print("\n[bold]  💡 改进建议[/bold]")
    for i, s in enumerate(result.suggestions[:2], 1):
        console.print(f"    {i}. [yellow]问题:[/yellow] {s.get('issue', '')}")
        console.print(f"       [green]建议:[/green] {s.get('better_approach', '')}")
        console.print(f"       [dim]示范: {s.get('example', '')}[/dim]")

if result.recommended_practice:
    ids = ", ".join(result.recommended_practice)
    console.print(f"\n[bold]  📚 推荐练习话术:[/bold] {ids}")

# ======================================================================
# 进度追踪
# ======================================================================
console.print("\n" + "─" * 55)
console.print("[bold cyan]📊 学员进度 (demo_user)[/bold cyan]")

report = engine.get_progress_report("demo_user")
p = report["progress"]
console.print(f"  总训练次数: [bold]{p['total_sessions']}[/bold]")

dims = p.get("dimensions", {})
if dims:
    console.print("\n  [bold]能力画像:[/bold]")
    for dim, score in sorted(dims.items()):
        bar = "█" * (int(score) // 5) + "░" * (20 - int(score) // 5)
        console.print(f"    {dim:12s} [dim]{bar}[/dim] {score:.0f}")

weak = report.get("weak_areas", [])
if weak:
    joined = ", ".join(weak)
    console.print(f"\n  [yellow]薄弱维度: {joined}[/yellow]")

rec = report.get("recommended_scenarios", [])
if rec:
    joined = ", ".join(rec)
    console.print(f"  [cyan]推荐场景: {joined}[/cyan]")

# ======================================================================
# 话术库浏览
# ======================================================================
console.print("\n" + "─" * 55)
console.print("[bold cyan]📖 话术知识库 (异议处理)[/bold cyan]")

scripts = engine.get_scripts_by_category("objections")
for s in scripts[:3]:
    triggers = " / ".join(s.get("trigger", [])[:3])
    console.print(f"\n  [bold]{s['id']}[/bold] [dim]| 触发: {triggers}[/dim]")
    console.print(f"  [green]{s['standard_response'][:120]}...[/green]")
    kps = " · ".join(s.get("key_points", [])[:3])
    console.print(f"  [dim]要点: {kps}[/dim]")
    console.print(f"  [dim]避免: {' · '.join(s.get('avoid', [])[:2])}[/dim]")

console.print(f"\n  ... 共 {len(scripts)} 条话术")

# ======================================================================
console.print("\n" + "─" * 55)
console.print("[bold green]✓ 完整流程演示结束[/bold green]")
console.print("[dim]启动交互式 CLI: python -m cli.main[/dim]")
console.print("[dim]启动 API 服务: uvicorn agent_skill.api_server:app --port 8080[/dim]")
console.print("[dim]启动 Web UI: streamlit run demo/app.py[/dim]\n")
