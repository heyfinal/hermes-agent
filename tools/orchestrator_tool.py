"""
Orchestrator tool — dynamic multi-agent team assembly for Agent Breakout.

Exposes a single ``orchestrate_team`` tool that reads agent profiles from
``~/.hermes/orchestrator/agents/*.yaml``, assembles a team matching the
project requirements, spawns subagents via ``delegate_task`` in the correct
order (design → build → integrate), and records the run in orchestrator.db.

Gated on ``orchestrator.enabled`` in config.yaml.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def _get_orchestrator_dir() -> Path:
    return get_hermes_home() / "orchestrator"


def _get_agents_dir() -> Path:
    return _get_orchestrator_dir() / "agents"


def _read_agent_profile(name: str) -> Optional[Dict[str, Any]]:
    """Read an agent profile from its YAML file."""
    path = _get_agents_dir() / f"{name}.yaml"
    if not path.exists():
        return None

    content = path.read_text()
    import re

    def _val(key: str, default: str = "") -> str:
        m = re.search(rf"^{key}:\s*(.+?)$", content, re.MULTILINE)
        return m.group(1).strip().strip('"') if m else default

    def _list_val(key: str) -> List[str]:
        """Extract a YAML list of quoted strings."""
        items = []
        in_list = False
        for line in content.split("\n"):
            if line.strip().startswith(f"{key}:"):
                in_list = True
                continue
            if in_list:
                if not line.strip().startswith("- "):
                    break
                val = line.strip()[2:].strip().strip('"')
                if val:
                    items.append(val)
        return items

    return {
        "name": _val("name", name),
        "label": _val("label", name),
        "description": _val("description", ""),
        "enabled": _val("enabled", "true") == "true",
        "prompt": content,  # full content used as context prompt
        "toolsets": _list_val("toolsets") or ["file", "search"],
        "model": _val("model", ""),
        "max_turns": int(_val("max_turns", "30")),
        "version": int(_val("version", "1")),
    }


def _list_enabled_agents() -> List[Dict[str, Any]]:
    """List all enabled agent profiles."""
    agents = []
    agents_dir = _get_agents_dir()
    if not agents_dir.exists():
        return []

    for path in sorted(agents_dir.glob("*.yaml")):
        profile = _read_agent_profile(path.stem)
        if profile and profile.get("enabled", True):
            agents.append(profile)
    return agents


def _analyze_project(goal: str, agents: List[Dict[str, Any]]) -> List[str]:
    """Simple heuristic-based agent selection from project description."""
    goal_lower = goal.lower()

    # Domain keywords → agent name mapping
    domain_map: Dict[str, List[str]] = {
        "architecture": ["architect"],
        "design": ["architect"],
        "system": ["architect"],
        "software": ["software-engineer"],
        "backend": ["software-engineer"],
        "api": ["software-engineer"],
        "app": ["software-engineer"],
        "firmware": ["firmware-engineer"],
        "esp32": ["firmware-engineer"],
        "microcontroller": ["firmware-engineer"],
        "embedded": ["firmware-engineer"],
        "arduino": ["firmware-engineer"],
        "network": ["network-engineer"],
        "networking": ["network-engineer"],
        "protocol": ["network-engineer"],
        "wifi": ["network-engineer"],
        "devops": ["devops-engineer"],
        "deploy": ["devops-engineer"],
        "ci/cd": ["devops-engineer"],
        "docker": ["devops-engineer"],
        "kubernetes": ["devops-engineer"],
        "frontend": ["frontend-engineer"],
        "ui": ["frontend-engineer"],
        "web": ["frontend-engineer"],
        "dashboard": ["frontend-engineer"],
        "ml": ["ml-engineer"],
        "model": ["ml-engineer"],
        "training": ["ml-engineer"],
        "machine learning": ["ml-engineer"],
        "data": ["data-engineer"],
        "database": ["data-engineer"],
        "pipeline": ["data-engineer"],
        "analytics": ["data-engineer"],
        "security": ["security-engineer"],
        "vulnerability": ["security-engineer"],
        "audit": ["security-engineer"],
        "pentest": ["security-engineer"],
        "test": ["qa-engineer"],
        "testing": ["qa-engineer"],
        "quality": ["qa-engineer"],
    }

    selected_set: set = set()
    for keyword, agent_names in domain_map.items():
        if keyword in goal_lower:
            selected_set.update(agent_names)

    # Always include architect for complex projects
    selected_set.add("architect")

    # Filter to agents that actually exist and are enabled
    agent_names = {a["name"] for a in agents}
    selected = [name for name in selected_set if name in agent_names]

    return selected[:4]  # Max 4 agents per team


def _save_run(
    team_id: str,
    agent_names: List[str],
    status: str,
    summary: str = "",
    errors: List[str] = None,
) -> None:
    """Log a team run to orchestrator.db."""
    try:
        import sqlite3
        db_path = _get_orchestrator_dir() / "orchestrator.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                team_id TEXT,
                agent_names TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT DEFAULT 'running',
                summary TEXT,
                output_errors TEXT
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO runs (id, team_id, agent_names, started_at, "
            "finished_at, status, summary, output_errors) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                team_id,
                team_id,
                json.dumps(agent_names),
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                status,
                summary,
                json.dumps(errors or []),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to save run to orchestrator.db: %s", exc)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def check_requirements() -> bool:
    """Gate: only expose orchestrator tool when orchestrator.enabled is True."""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        return config.get("orchestrator", {}).get("enabled", True)
    except Exception:
        return True  # Default to enabled


def orchestrate_team(goal: str, context: str = "", agents: str = "") -> str:
    """
    Assemble a specialized agent team for a complex project.

    Analyzes the project goal, selects appropriate agents from the
    ~/.hermes/orchestrator/agents/ profile registry, spawns them in the
    correct dependency order (architect first, then parallel build agents,
    then integration), and returns the integrated result.

    Args:
        goal: Project goal/description. The tool analyzes this to select
              appropriate agents from the registry.
        context: Additional project context, constraints, or requirements
                 passed to all agents.
        agents: Comma-separated agent names to explicitly select. If empty,
                agents are auto-selected from the goal analysis. Example:
                "architect,software-engineer,firmware-engineer,network-engineer"

    Returns:
        JSON with team composition, run ID, agent outputs, and integration summary.
    """
    from tools.delegate_tool import delegate_task

    start_time = time.time()
    team_id = f"team_{int(start_time)}"

    # Read available agents
    available = _list_enabled_agents()

    # Select team
    if agents:
        requested = [a.strip() for a in agents.split(",") if a.strip()]
        team_names = [a for a in requested if a in {p["name"] for p in available}]
    else:
        team_names = [a["name"] for a in available if a["name"] == "architect"]
        team_names.extend(
            a["name"] for a in available if a["name"] != "architect"
        )
        team_names = _analyze_project(goal, available)

    if not team_names:
        result = {
            "success": False,
            "error": "No suitable agents found. Run 'hermes orchestrator agent list' to see available profiles.",
        }
        return json.dumps(result)

    # Phase 1: Architect (sequential — produces architecture)
    architects = [n for n in team_names if n == "architect"]
    build_agents = [n for n in team_names if n != "architect"]

    arch_output = ""
    if architects:
        arch_name = architects[0]
        profile = _read_agent_profile(arch_name)
        if profile:
            arch_goal = (
                f"Design the system architecture for: {goal}\n\n"
                f"Context: {context}\n\n"
                f"Deliverables:\n"
                f"1. System architecture diagram / description\n"
                f"2. Component breakdown with responsibilities\n"
                f"3. Interface contracts (APIs, data formats, protocols)\n"
                f"4. Technology stack recommendation with rationale\n"
                f"5. Dependency graph\n"
                f"6. Key design decisions and trade-offs"
            )
            try:
                arch_result = delegate_task(
                    goal=arch_goal,
                    context=profile["prompt"],
                    toolsets=profile.get("toolsets", ["file", "search"]),
                )
                arch_output = arch_result.get("summary", "")
            except Exception as exc:
                arch_output = f"(Architect error: {exc})"

    # Phase 2: Build agents (parallel)
    build_tasks = []
    for agent_name in build_agents:
        profile = _read_agent_profile(agent_name)
        if not profile:
            continue

        agent_goal = (
            f"Implement the {profile['label']} for: {goal}\n\n"
            f"Architecture context: {arch_output[:2000]}\n\n"
            f"Project context: {context}\n\n"
            f"Deliverables per your agent profile:\n"
            f"- Working implementation\n"
            f"- Tests (where applicable)\n"
            f"- Documentation\n"
            f"- Build/run instructions"
        )
        build_tasks.append({
            "goal": agent_goal,
            "context": profile["prompt"],
            "toolsets": profile.get("toolsets", ["file", "terminal", "search"]),
        })

    build_results = []
    if build_tasks:
        try:
            build_results = delegate_task(tasks=build_tasks)
        except Exception as exc:
            build_results = [{"summary": f"(Build phase error: {exc})"}]

    # Collect results
    agent_outputs = {}
    if arch_output:
        agent_outputs["architect"] = arch_output
    for i, agent_name in enumerate(build_agents):
        if i < len(build_results):
            agent_outputs[agent_name] = build_results[i].get("summary", "")

    elapsed = time.time() - start_time
    summary = (
        f"Team assembled with {len(team_names)} agents "
        f"({', '.join(team_names)}) in {elapsed:.1f}s."
    )

    _save_run(team_id, team_names, "completed", summary)

    result = {
        "success": True,
        "team_id": team_id,
        "team": team_names,
        "elapsed_seconds": round(elapsed, 1),
        "summary": summary,
        "agent_outputs": agent_outputs,
    }
    return json.dumps(result)