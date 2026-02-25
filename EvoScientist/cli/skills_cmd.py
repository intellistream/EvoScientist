"""Slash commands for skill management: /skills, /install-skill, /uninstall-skill."""

from ..stream.display import console
from .agent import _shorten_path


def _cmd_list_skills() -> None:
    """List all available skills (user and system)."""
    from ..tools.skills_manager import list_skills
    from ..paths import USER_SKILLS_DIR

    skills = list_skills(include_system=True)

    if not skills:
        console.print("[dim]No skills available.[/dim]")
        console.print("[dim]Install with:[/dim] /install-skill <path-or-url>")
        console.print(f"[dim]Skills directory:[/dim] [cyan]{_shorten_path(str(USER_SKILLS_DIR))}[/cyan]")
        console.print()
        return

    user_skills = [s for s in skills if s.source == "user"]
    system_skills = [s for s in skills if s.source == "system"]

    if user_skills:
        console.print(f"[bold]User Skills[/bold] ({len(user_skills)}):")
        for skill in user_skills:
            console.print(f"  [green]{skill.name}[/green] - {skill.description}")

    if user_skills and system_skills:
        console.print()

    if system_skills:
        console.print(f"[bold]Built-in Skills[/bold] ({len(system_skills)}):")
        for skill in system_skills:
            console.print(f"  [cyan]{skill.name}[/cyan] - {skill.description}")

    console.print(f"\n[dim]User skills folder:[/dim] [green]{_shorten_path(str(USER_SKILLS_DIR))}[/green]")
    console.print()


def _cmd_install_skill(source: str) -> None:
    """Install a skill from local path or GitHub URL."""
    from ..tools.skills_manager import install_skill

    if not source:
        console.print("[red]Usage:[/red] /install-skill <path-or-url>")
        console.print("[dim]Examples:[/dim]")
        console.print("  /install-skill ./my-skill")
        console.print("  /install-skill https://github.com/user/repo/tree/main/skill-name")
        console.print("  /install-skill user/repo@skill-name")
        console.print()
        return

    console.print(f"[dim]Installing skill from:[/dim] {source}")

    result = install_skill(source)

    if result.get("batch"):
        # Batch install — multiple skills
        for item in result.get("installed", []):
            console.print(f"[green]Installed:[/green] {item['name']}")
            console.print(f"  [dim]Description:[/dim] {item.get('description', '(none)')}")
            console.print(f"  [dim]Path:[/dim] [cyan]{_shorten_path(item['path'])}[/cyan]")
        for item in result.get("failed", []):
            console.print(f"[red]Failed:[/red] {item['name']} — {item['error']}")
        installed_count = len(result.get("installed", []))
        if installed_count:
            console.print(f"\n[green]{installed_count} skill(s) installed.[/green]")
            console.print("[dim]Reload with /new to apply.[/dim]")
    elif result["success"]:
        console.print(f"[green]Installed:[/green] {result['name']}")
        console.print(f"[dim]Description:[/dim] {result.get('description', '(none)')}")
        console.print(f"[dim]Path:[/dim] [cyan]{_shorten_path(result['path'])}[/cyan]")
        console.print()
        console.print("[dim]Reload with /new to apply.[/dim]")
    else:
        console.print(f"[red]Failed:[/red] {result['error']}")
    console.print()


def _cmd_uninstall_skill(name: str) -> None:
    """Uninstall a user-installed skill."""
    from ..tools.skills_manager import uninstall_skill

    if not name:
        console.print("[red]Usage:[/red] /uninstall-skill <skill-name>")
        console.print("[dim]Use /skills to see installed skills.[/dim]")
        console.print()
        return

    result = uninstall_skill(name)

    if result["success"]:
        console.print(f"[green]Uninstalled:[/green] {name}")
        console.print("[dim]Reload with /new to apply.[/dim]")
    else:
        console.print(f"[red]Failed:[/red] {result['error']}")
    console.print()
