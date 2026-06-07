"""
Orchestrator subcommand for hermes CLI.

Handles agent profile management, team assembly, run history, and evolution
cycle for the Agent Breakout multi-agent orchestration framework.

Usage:
    hermes orchestrator                     # Status overview
    hermes orchestrator agent list          # List agent profiles
    hermes orchestrator agent show <name>   # Show profile detail
    hermes orchestrator agent create        # Interactive wizard
    hermes orchestrator agent edit <name>   # Open in $EDITOR
    hermes orchestrator agent remove <name> # Remove a profile
    hermes orchestrator agent toggle <name> # Enable/disable

    hermes orchestrator team assemble       # Interactive team builder
    hermes orchestrator team history        # Show past runs

    hermes orchestrator evolution run       # Trigger evolution cycle now
    hermes orchestrator evolution status    # When last run, what changed
    hermes orchestrator evolution config    # Show/set interval
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
ORCHESTRATOR_DIR = HERMES_HOME / "orchestrator"
AGENTS_DIR = ORCHESTRATOR_DIR / "agents"
TEAMS_DIR = ORCHESTRATOR_DIR / "teams"
ARTIFACTS_DIR = ORCHESTRATOR_DIR / "artifacts"
EVOLUTION_DIR = ORCHESTRATOR_DIR / "evolution"
DB_PATH = ORCHESTRATOR_DIR / "orchestrator.db"

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def _ensure_orchestrator_dir() -> None:
    """Create the orchestrator directory structure if it doesn't exist."""
    ORCHESTRATOR_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
    _create_default_agents_if_empty()


def _create_default_agents_if_empty() -> None:
    """Seed default agent profiles if the agents directory is empty."""
    if any(AGENTS_DIR.iterdir()):
        return

    defaults: Dict[str, Dict[str, Any]] = {
        "architect": {
            "label": "Architect Agent",
            "description": "System architecture, project structure, technical strategy",
            "prompt": (
                "You are the Architect Agent. Your role is to design system architecture,\n"
                "define scope, establish technical direction, and coordinate interfaces\n"
                "between components. You do NOT implement — you specify what should be built\n"
                "and how components connect.\n\n"
                "Deliverables:\n"
                "1. System architecture diagram / description\n"
                "2. Component breakdown with responsibilities\n"
                "3. Interface contracts (APIs, data formats, protocols)\n"
                "4. Technology stack recommendation\n"
                "5. Dependency graph\n"
                "6. Key design decisions documented\n\n"
                "Constraints:\n"
                "- Stay at the design level unless explicitly asked to implement\n"
                "- Document all interface contracts precisely\n"
                "- Flag risky decisions with mitigation options"
            ),
            "toolsets": ["file", "search", "web"],
            "model": "",
            "max_turns": 30,
            "output_contract": {
                "files": ["ARCHITECTURE.md", "INTERFACES.md"],
                "format": "markdown",
            },
            "quality_gates": [
                "Every component has clear inputs and outputs",
                "No circular dependencies",
                "Technology choices justified",
                "Scalability and failure modes addressed",
            ],
            "version": 1,
        },
        "software-engineer": {
            "label": "Software Engineer Agent",
            "description": "Application logic, APIs, libraries, services",
            "prompt": (
                "You are the Software Engineer Agent. You implement application logic,\n"
                "build APIs, develop integrations, write automation workflows, and maintain\n"
                "code quality. You work from the architect's interface contracts.\n\n"
                "Deliverables:\n"
                "1. Working code implementing the specified components\n"
                "2. Unit tests and integration tests\n"
                "3. API documentation\n"
                "4. Build/run instructions\n"
                "5. Error handling and logging\n\n"
                "Principles:\n"
                "- Write testable, maintainable code\n"
                "- Follow language-specific best practices\n"
                "- Handle errors gracefully with meaningful messages\n"
                "- Include logging for operational visibility\n"
                "- Do not over-engineer — prefer simple solutions that work"
            ),
            "toolsets": ["terminal", "file", "search"],
            "model": "",
            "max_turns": 30,
            "output_contract": {
                "files": ["README.md"],
                "format": "code",
            },
            "quality_gates": [
                "Tests pass",
                "No lint errors",
                "No TODO/FIXME in production code",
                "Error paths handled",
            ],
            "version": 1,
        },
        "firmware-engineer": {
            "label": "Firmware / Embedded Engineer Agent",
            "description": "MCU firmware, RTOS, hardware abstraction, peripheral drivers",
            "prompt": (
                "You are the Firmware/Embedded Engineer Agent. You handle hardware-near\n"
                "development: microcontroller firmware, driver implementation, wireless\n"
                "communication, power optimization, and real-time constraints.\n\n"
                "Deliverables:\n"
                "1. Firmware source code (ESP-IDF, Arduino, etc.)\n"
                "2. Pinout and peripheral configuration documentation\n"
                "3. Build/flash instructions\n"
                "4. Communication protocol implementation\n\n"
                "Constraints:\n"
                "- Document hardware assumptions (pin assignments, voltage levels)\n"
                "- Include error recovery for hardware failures\n"
                "- Consider watchdog timers and brownout scenarios\n"
                "- Optimize for the target MCU's constraints (RAM, flash, clock)"
            ),
            "toolsets": ["terminal", "file", "search", "web"],
            "model": "",
            "max_turns": 30,
            "output_contract": {
                "files": ["FIRMWARE.md", "pinout.md"],
                "format": "code",
            },
            "quality_gates": [
                "Compiles without errors",
                "Watchdog configured",
                "Error paths for peripheral failures",
                "Hardware interactions documented",
            ],
            "version": 1,
        },
        "network-engineer": {
            "label": "Network Engineer Agent",
            "description": "Network protocols, diagnostics, security, monitoring",
            "prompt": (
                "You are the Network Engineer Agent. You design network functionality,\n"
                "implement network services, configure diagnostics, build monitoring tools,\n"
                "and ensure networking best practices and security.\n\n"
                "Deliverables:\n"
                "1. Network architecture design\n"
                "2. Network service configuration\n"
                "3. Diagnostic tools or scripts\n"
                "4. Monitoring and alerting setup\n"
                "5. Security hardening recommendations\n\n"
                "Principles:\n"
                "- Follow established networking standards (RFCs)\n"
                "- Document all port, protocol, and addressing decisions\n"
                "- Consider failure modes: link loss, latency, congestion\n"
                "- Include security at every layer"
            ),
            "toolsets": ["terminal", "file", "search", "web"],
            "model": "",
            "max_turns": 30,
            "output_contract": {
                "files": ["NETWORK.md"],
                "format": "markdown",
            },
            "quality_gates": [
                "No default/weak credentials in configs",
                "Firewall rules documented with rationale",
                "Redundancy/failover addressed",
                "Monitoring thresholds defined",
            ],
            "version": 1,
        },
        "devops-engineer": {
            "label": "DevOps Engineer Agent",
            "description": "CI/CD, containerization, infrastructure-as-code, deployment",
            "prompt": (
                "You are the DevOps Engineer Agent. You build and maintain infrastructure\n"
                "and deployment pipelines so the team can ship reliably and efficiently.\n\n"
                "Deliverables:\n"
                "1. CI/CD pipeline configuration\n"
                "2. Container definitions (Dockerfile, K8s manifests)\n"
                "3. Infrastructure-as-code (Terraform, Ansible)\n"
                "4. Deployment runbook\n"
                "5. Monitoring and alerting configuration\n\n"
                "Principles:\n"
                "- Infrastructure is code — version-controlled and reproducible\n"
                "- Immutable deployments preferred over in-place updates\n"
                "- Secrets never in source — use vault/secret-store\n"
                "- Health checks, readiness probes, graceful shutdown"
            ),
            "toolsets": ["terminal", "file", "search", "web"],
            "model": "",
            "max_turns": 30,
            "output_contract": {
                "files": ["DEPLOY.md"],
                "format": "markdown",
            },
            "quality_gates": [
                "Pipeline runs to completion",
                "Secrets not hardcoded",
                "Rollback procedure documented",
                "Resource limits set",
            ],
            "version": 1,
        },
    }

    for name, data in defaults.items():
        path = AGENTS_DIR / f"{name}.yaml"
        if not path.exists():
            _write_agent_yaml(name, data)


def _write_agent_yaml(name: str, data: Dict[str, Any]) -> None:
    """Write an agent profile YAML file."""
    lines = [
        f"name: {name}",
        f"label: {data['label']}",
        f"description: {data['description']}",
        f"enabled: true",
        "",
        "prompt: |",
    ]
    for line in data["prompt"].split("\n"):
        lines.append(f"  {line}" if line.strip() else "  ")

    lines.append("")
    lines.append("toolsets:")
    for t in data.get("toolsets", []):
        lines.append(f'  - "{t}"')

    lines.append(f'model: "{data.get("model", "")}"')
    lines.append(f"max_turns: {data.get('max_turns', 30)}")
    lines.append("")
    lines.append("quality_gates:")
    for g in data.get("quality_gates", []):
        lines.append(f'  - "{g}"')

    lines.append("")
    lines.append(f"version: {data.get('version', 1)}")
    lines.append(f"last_updated: {time.strftime('%Y-%m-%d')}")

    path = AGENTS_DIR / f"{name}.yaml"
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Agent commands
# ---------------------------------------------------------------------------


def agent_list() -> None:
    """List all agent profiles."""
    _ensure_orchestrator_dir()
    agents = sorted(AGENTS_DIR.glob("*.yaml"))
    if not agents:
        print("No agent profiles found. Run 'hermes orchestrator agent create'")
        return

    print(f"Agent profiles ({len(agents)}):")
    print(f"{'NAME':<25} {'LABEL':<30} {'VERSION':<8} {'ENABLED'}")
    print("-" * 75)
    for path in agents:
        name = path.stem
        _show_agent_summary(name)


def _show_agent_summary(name: str) -> None:
    """Print a single agent's summary line."""
    path = AGENTS_DIR / f"{name}.yaml"
    if not path.exists():
        return
    content = path.read_text()
    label = _yaml_val(content, "label", name)
    ver = _yaml_val(content, "version", "1")
    enabled = _yaml_val(content, "enabled", "true")
    enabled_str = "\u2713" if enabled == "true" else "\u2717"
    print(f"{name:<25} {label:<30} v{ver:<6} {enabled_str}")


def _yaml_val(content: str, key: str, default: str = "") -> str:
    """Extract a simple YAML scalar value by key."""
    import re
    m = re.search(rf"^{key}:\s*(.+?)$", content, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"')
    return default


def agent_show(name: str) -> None:
    """Show full agent profile."""
    _ensure_orchestrator_dir()
    path = AGENTS_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"Agent '{name}' not found.")
        print(f"Available: {', '.join(p.stem for p in sorted(AGENTS_DIR.glob('*.yaml')))}")
        return
    content = path.read_text()
    print(f"=== Agent: {name} ===")
    print(content)


def agent_create() -> None:
    """Interactive agent creation wizard."""
    _ensure_orchestrator_dir()
    print("=== New Agent Profile ===")
    name = input("Agent name (lowercase, hyphens): ").strip()
    if not name:
        print("Cancelled.")
        return
    path = AGENTS_DIR / f"{name}.yaml"
    if path.exists():
        print(f"Agent '{name}' already exists.")
        return

    label = input("Label (human-readable name): ").strip() or name
    desc = input("Description: ").strip() or ""

    print("Enter system prompt (end with '---' on its own line):")
    prompt_lines = []
    while True:
        line = input()
        if line.strip() == "---":
            break
        prompt_lines.append(line)
    prompt = "\n".join(prompt_lines)

    data = {
        "label": label,
        "description": desc,
        "prompt": prompt,
        "toolsets": ["file", "search"],
        "model": "",
        "max_turns": 30,
        "quality_gates": [],
        "version": 1,
    }
    _write_agent_yaml(name, data)
    print(f"Agent '{name}' created.")


def agent_edit(name: str) -> None:
    """Open agent profile in $EDITOR."""
    _ensure_orchestrator_dir()
    path = AGENTS_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"Agent '{name}' not found.")
        return
    editor = os.environ.get("EDITOR", "nano")
    subprocess.check_call([editor, str(path)])


def agent_remove(name: str) -> None:
    """Remove an agent profile."""
    _ensure_orchestrator_dir()
    path = AGENTS_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"Agent '{name}' not found.")
        return
    path.unlink()
    print(f"Agent '{name}' removed.")


def agent_toggle(name: str) -> None:
    """Toggle agent enabled/disabled."""
    _ensure_orchestrator_dir()
    path = AGENTS_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"Agent '{name}' not found.")
        return
    content = path.read_text()
    if "enabled: true" in content:
        content = content.replace("enabled: true", "enabled: false")
        print(f"Agent '{name}' disabled.")
    else:
        content = content.replace("enabled: false", "enabled: true")
        print(f"Agent '{name}' enabled.")
    path.write_text(content)


# ---------------------------------------------------------------------------
# Team commands
# ---------------------------------------------------------------------------


def team_assemble() -> None:
    """Interactive team assembly (stub — reads from orchestrate_tool)."""
    _ensure_orchestrator_dir()
    from hermes_cli.commands import print_markdown

    print("Interactive team assembly (select agents for your project):")
    print()

    agents = sorted(AGENTS_DIR.glob("*.yaml"))
    if not agents:
        print("No agent profiles found. Create some first with 'hermes orchestrator agent create'")
        return

    print("Available agents:")
    for i, path in enumerate(agents, 1):
        name = path.stem
        content = path.read_text()
        label = _yaml_val(content, "label", name)
        enabled = _yaml_val(content, "enabled", "true")
        marker = "\u2713" if enabled == "true" else "\u2717"
        print(f"  [{i}] {marker} {name:<25} {label}")

    print()
    print("Select agents by number (space-separated), or 'all' for all enabled:")
    selection = input("> ").strip()
    if not selection:
        print("Cancelled.")
        return

    if selection.lower() == "all":
        selected = [p.stem for p in agents if _yaml_val(p.read_text(), "enabled", "true") == "true"]
    else:
        indices = []
        for part in selection.split():
            try:
                idx = int(part) - 1
                if 0 <= idx < len(agents):
                    indices.append(idx)
            except ValueError:
                pass
        selected = [agents[i].stem for i in indices]

    if not selected:
        print("No agents selected.")
        return

    print(f"\nTeam assembled: {', '.join(selected)}")
    print("To run this team, use the 'orchestrate_team' tool in a session,")
    print("or submit your project description to start the orchestration.")


def team_history() -> None:
    """Show past team runs from orchestrator.db."""
    _ensure_orchestrator_dir()
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute(
            "SELECT id, team_id, started_at, status, summary "
            "FROM runs ORDER BY started_at DESC LIMIT 20"
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            print("No team runs yet.")
            return

        print(f"{'RUN ID':<20} {'TEAM':<8} {'DATE':<20} {'STATUS':<12} {'SUMMARY'}")
        print("-" * 80)
        for row_id, team_id, started, status, summary in rows:
            s = (summary or "")[:50]
            print(f"{row_id:<20} {team_id:<8} {started:<20} {status:<12} {s}")
    except (ImportError, sqlite3.OperationalError):
        print("No run history yet. Use 'orchestrate_team' tool in a session to create runs.")


# ---------------------------------------------------------------------------
# Evolution commands
# ---------------------------------------------------------------------------


def evolution_run() -> None:
    """Trigger the evolution cycle."""
    _ensure_orchestrator_dir()
    report_path = EVOLUTION_DIR / "report.md"
    history_path = EVOLUTION_DIR / "history.jsonl"

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# Agent Evolution Report — {timestamp}",
        "",
        "## Environment",
    ]

    # Check tooling versions
    import shutil as _sh
    checks = [
        ("Python", "python3", "--version"),
        ("Node.js", "node", "--version"),
        ("Rust", "rustc", "--version"),
        ("Docker", "docker", "--version"),
        ("Swift", "swift", "--version"),
        ("Go", "go", "version"),
    ]
    for name, cmd, flag in checks:
        p = _sh.which(cmd)
        if p:
            try:
                result = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=10)
                ver = result.stdout.strip().split("\n")[0] if result.stdout else "unknown"
                lines.append(f"- {name}: {ver}")
            except Exception:
                lines.append(f"- {name}: (check failed)")

    lines.append("")
    lines.append("## Agent Profiles")
    agents = sorted(AGENTS_DIR.glob("*.yaml"))
    lines.append(f"- Total: {len(agents)}")
    for path in agents:
        lines.append(f"  - {path.stem}")

    lines.append("")
    lines.append("## Status")
    lines.append("Evolution cycle completed. No automatic prompt changes made.")
    lines.append("Review prompts manually or enable auto_update_profiles in config.")

    report_path.write_text("\n".join(lines) + "\n")

    # Log to history
    entry = json.dumps({
        "ran_at": timestamp,
        "agent_count": len(agents),
        "status": "completed",
    })
    with open(history_path, "a") as f:
        f.write(entry + "\n")

    print(f"Evolution cycle complete. Report: {report_path}")
    print(lines[-2])
    print(lines[-1])


def evolution_status() -> None:
    """Show when evolution last ran and what changed."""
    _ensure_orchestrator_dir()
    report_path = EVOLUTION_DIR / "report.md"
    if not report_path.exists():
        print("No evolution runs yet.")
        return
    content = report_path.read_text()
    first_line = content.split("\n")[0] if content else "No report content"
    print(first_line)
    print()
    print("Full report:", report_path)


def evolution_config() -> None:
    """Show evolution configuration."""
    from hermes_cli.config import load_config
    config = load_config()
    evo = config.get("orchestrator", {}).get("evolution", {})
    print("Evolution settings:")
    print(f"  Enabled:              {evo.get('enabled', True)}")
    print(f"  Interval:             {evo.get('interval', 'weekly')}")
    print(f"  Day:                  {evo.get('day', 0)}  (0=Sunday)")
    print(f"  Time (UTC):           {evo.get('time', '03:00')}")
    print(f"  Auto-update prompts:  {evo.get('auto_update_profiles', True)}")
    print(f"  Notify on change:     {evo.get('notify_on_change', True)}")
    print()
    print("To change: hermes config set orchestrator.evolution.interval daily")


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


def orchestrator_status() -> None:
    """Show orchestrator status overview."""
    _ensure_orchestrator_dir()
    agents = list(AGENTS_DIR.glob("*.yaml"))
    enabled = sum(1 for p in agents if "enabled: true" in p.read_text())
    print(f"Agent profiles: {len(agents)} ({enabled} enabled)")
    print()

    if agents:
        print("Profiles:")
        for path in sorted(agents):
            _show_agent_summary(path.stem)

    print()
    evolution_report = EVOLUTION_DIR / "report.md"
    if evolution_report.exists():
        first = evolution_report.read_text().split("\n")[0]
        print(f"Last evolution: {first}")
    else:
        print("Last evolution: never")

    print()
    print("Path:", ORCHESTRATOR_DIR)


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------


def orchestrator_command(args) -> int:
    """Dispatch orchestrator subcommands.

    Called from hermes_cli/main.py:cmd_orchestrator.
    """
    sub = getattr(args, "orchestrator_command", None)
    if sub in {None, ""}:
        orchestrator_status()
        return 0

    if sub == "agent":
        agent_sub = getattr(args, "orchestrator_agent_action", None)
        if agent_sub in {None, ""}:
            agent_list()
        elif agent_sub == "list":
            agent_list()
        elif agent_sub == "show":
            agent_show(args.name)
        elif agent_sub == "create":
            agent_create()
        elif agent_sub == "edit":
            agent_edit(args.name)
        elif agent_sub == "remove":
            agent_remove(args.name)
        elif agent_sub == "toggle":
            agent_toggle(args.name)
        else:
            print(f"Unknown agent subcommand: {agent_sub}")
            return 1

    elif sub == "team":
        team_sub = getattr(args, "orchestrator_team_action", None)
        if team_sub in {None, ""}:
            print("Usage: hermes orchestrator team assemble|history")
            return 1
        elif team_sub == "assemble":
            team_assemble()
        elif team_sub == "history":
            team_history()
        else:
            print(f"Unknown team subcommand: {team_sub}")
            return 1

    elif sub == "evolution":
        evo_sub = getattr(args, "orchestrator_evolution_action", None)
        if evo_sub in {None, ""}:
            evolution_status()
        elif evo_sub == "run":
            evolution_run()
        elif evo_sub == "status":
            evolution_status()
        elif evo_sub == "config":
            evolution_config()
        else:
            print(f"Unknown evolution subcommand: {evo_sub}")
            return 1

    else:
        print(f"Unknown orchestrator subcommand: {sub}")
        return 1

    return 0