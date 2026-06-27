"""Regression tests for backend-visible skill-directory rendering (#41541).

``${HERMES_SKILL_DIR}``, the ``[Skill directory: ...]`` header, and the
supporting-file/script hints must resolve to the path the agent can actually
reach on the active terminal backend.  On Docker/Singularity/Modal that is
``/root/.hermes/skills/<name>``; on SSH/Daytona it is
``<remote_home>/.hermes/skills/<name>``; on the local backend the host path is
preserved.  Before the fix all three surfaces emitted the raw HOST path, so
bundled skill scripts (e.g. ``${HERMES_SKILL_DIR}/scripts/todo``) were
unrunnable inside the sandbox.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_skill(hermes_home: Path) -> Path:
    """Create a local skill dir with a bundled script under HERMES_HOME."""
    skill_dir = hermes_home / "skills" / "todo-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "todo").write_text("#!/usr/bin/env bash\n")
    return skill_dir


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


# ── map_skill_dir_for_backend ──────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["docker", "singularity", "modal"])
def test_container_backends_map_to_root_hermes(backend, hermes_home, monkeypatch):
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", backend)
    # No live environment created yet — TERMINAL_ENV drives the container base.
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == "/root/.hermes/skills/todo-skill"
    # The bundled script is reachable under the container-visible path.
    assert f"{mapped}/scripts/todo" == "/root/.hermes/skills/todo-skill/scripts/todo"


@pytest.mark.parametrize("backend", ["ssh", "daytona"])
def test_remote_backends_map_to_remote_home(backend, hermes_home, monkeypatch):
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", backend)
    env = SimpleNamespace(_remote_home="/home/remoteuser")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: env)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == "/home/remoteuser/.hermes/skills/todo-skill"


def test_local_backend_preserves_host_path(hermes_home, monkeypatch):
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == str(skill_dir)


def test_unknown_backend_falls_back_to_host_path(hermes_home, monkeypatch):
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "some-future-backend")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == str(skill_dir)


def test_dir_outside_any_mount_falls_back_to_host_path(hermes_home, tmp_path, monkeypatch):
    from agent import skill_path_mapping

    # A skill dir that is NOT under HERMES_HOME/skills and not a registered
    # external dir must not be given a bogus container path.
    _make_skill(hermes_home)
    stray = tmp_path / "elsewhere" / "stray-skill"
    stray.mkdir(parents=True)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(stray)

    assert mapped == str(stray)


def test_skills_tree_with_symlink_still_maps_original_host_path(
    hermes_home, tmp_path, monkeypatch
):
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    external = tmp_path / "external-skill"
    external.mkdir()
    (hermes_home / "skills" / "linked-skill").symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == "/root/.hermes/skills/todo-skill"


def test_symlink_safe_skills_mount_root_is_stable(hermes_home, tmp_path, monkeypatch):
    from tools import credential_files

    skill_dir = _make_skill(hermes_home)
    external = tmp_path / "external-skill"
    external.mkdir()
    (hermes_home / "skills" / "linked-skill").symlink_to(external, target_is_directory=True)

    first = credential_files.get_skills_directory_mount()[0]["host_path"]
    marker = Path(first) / "mounted-marker.txt"
    marker.write_text("would be visible through an active bind mount")

    second = credential_files.get_skills_directory_mount()[0]["host_path"]

    assert second == first
    assert Path(first).is_dir()
    assert (Path(second) / "todo-skill" / "scripts" / "todo").is_file()
    assert not marker.exists()
    assert skill_dir.exists()


# ── substitute_template_vars uses the mapped path ──────────────────────────


def test_template_var_substitution_uses_container_path(hermes_home, monkeypatch):
    from agent import skill_preprocessing

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    out = skill_preprocessing.substitute_template_vars(
        "Run ${HERMES_SKILL_DIR}/scripts/todo", skill_dir, session_id=None
    )

    assert out == "Run /root/.hermes/skills/todo-skill/scripts/todo"


def test_legacy_skill_dir_substitution_uses_container_path(hermes_home, monkeypatch):
    from agent import skill_preprocessing

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    out = skill_preprocessing.substitute_template_vars(
        "uv run python3 SKILL_DIR/scripts/fetch_transcript.py", skill_dir, session_id=None
    )

    assert out == "uv run python3 /root/.hermes/skills/todo-skill/scripts/fetch_transcript.py"


def test_template_var_substitution_local_keeps_host_path(hermes_home, monkeypatch):
    from agent import skill_preprocessing

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    out = skill_preprocessing.substitute_template_vars(
        "Run ${HERMES_SKILL_DIR}/scripts/todo", skill_dir, session_id=None
    )

    assert out == f"Run {skill_dir}/scripts/todo"


# ── _build_skill_message: all three surfaces agree (the core invariant) ─────


def _build_message(skill_dir: Path):
    from agent import skill_commands

    loaded_skill = {
        "content": "Use ${HERMES_SKILL_DIR}/scripts/todo to manage tasks.",
        "linked_files": {},
    }
    return skill_commands._build_skill_message(
        loaded_skill,
        skill_dir,
        activation_note="[activation]",
        session_id=None,
    )


def test_all_agent_visible_surfaces_agree_on_container_path(hermes_home, monkeypatch):
    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    msg = _build_message(skill_dir)

    container = "/root/.hermes/skills/todo-skill"
    # The host path must NOT leak into the prompt on a remote backend.
    assert str(skill_dir) not in msg
    # 1) ${HERMES_SKILL_DIR} substitution in the skill body.
    assert f"Use {container}/scripts/todo" in msg
    # 2) [Skill directory: ...] header.
    assert f"[Skill directory: {container}]" in msg
    # 3) supporting-file / script hint.
    assert f"{container}/scripts/todo" in msg
    assert f"node {container}/scripts/foo.js" in msg


def test_all_agent_visible_surfaces_preserve_host_path_locally(hermes_home, monkeypatch):
    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    msg = _build_message(skill_dir)

    assert f"[Skill directory: {skill_dir}]" in msg
    assert f"{skill_dir}/scripts/todo" in msg


# ── Copilot follow-ups (#41561 review) ─────────────────────────────────────


def test_windows_supporting_file_renders_posix_against_container_hint(
    hermes_home, monkeypatch
):
    """A linked_files entry collected with Windows separators must render as a
    clean POSIX path when the backend hint_dir is a PurePosixPath; otherwise
    PurePosixPath / "scripts\\todo" embeds backslashes into the POSIX join,
    yielding a mixed-separator path the backend cannot resolve.
    """
    from agent import skill_commands

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    loaded_skill = {
        "content": "body",
        # Windows-host-collected relative path (backslash separator).
        "linked_files": {"scripts": ["scripts\\todo"]},
    }
    msg = skill_commands._build_skill_message(
        loaded_skill, skill_dir, activation_note="[activation]", session_id=None
    )

    container = "/root/.hermes/skills/todo-skill"
    # The resolved backend path joins cleanly as POSIX...
    assert f"{container}/scripts/todo" in msg
    # ...with no mixed-separator backend path (the pre-fix bug embedded the
    # backslash into the POSIX join: ``/root/.hermes/.../scripts\todo``).
    assert f"{container}/scripts\\todo" not in msg
    assert "\\todo" not in msg.split("  ->  ", 1)[1]


def test_ssh_backend_without_live_env_no_longer_yields_tilde_path(
    hermes_home, monkeypatch
):
    """The ssh backend used to return ``~/.hermes`` (shell-tilde dependent and
    invalid in non-shell path contexts).  Without a live environment exposing
    ``_remote_home`` it must now fall back to the host path, never a tilde.
    """
    from agent import skill_path_mapping

    skill_dir = _make_skill(hermes_home)
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.setattr(skill_path_mapping, "_active_terminal_env", lambda task_id: None)

    mapped = skill_path_mapping.map_skill_dir_for_backend(skill_dir)

    assert mapped == str(skill_dir)
    assert "~" not in mapped


def test_skill_view_reports_only_backend_visible_skill_dir(hermes_home, monkeypatch):
    from tools import skills_tool

    skill_dir = _make_skill(hermes_home)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: todo-skill\ndescription: Test skill\n---\n"
        "Run ${HERMES_SKILL_DIR}/scripts/todo\n"
        "uv run python3 SKILL_DIR/scripts/fetch_transcript.py\n"
    )
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", hermes_home / "skills")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    payload = json.loads(skills_tool.skill_view("todo-skill", task_id="test-task"))

    assert payload["success"] is True
    assert payload["skill_dir"] == "/root/.hermes/skills/todo-skill"
    assert "host_skill_dir" not in payload
    assert "Run /root/.hermes/skills/todo-skill/scripts/todo" in payload["content"]
    assert "uv run python3 /root/.hermes/skills/todo-skill/scripts/fetch_transcript.py" in payload["content"]


def test_skill_view_can_include_host_skill_dir_for_internal_callers(
    hermes_home, monkeypatch
):
    from tools import skills_tool

    skill_dir = _make_skill(hermes_home)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: todo-skill\ndescription: Test skill\n---\nbody\n"
    )
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", hermes_home / "skills")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    payload = json.loads(
        skills_tool.skill_view(
            "todo-skill",
            task_id="test-task",
            include_host_skill_dir=True,
        )
    )

    assert payload["skill_dir"] == "/root/.hermes/skills/todo-skill"
    assert payload["host_skill_dir"] == str(skill_dir)


def test_slash_skill_loading_uses_host_skill_dir_for_file_enumeration(
    hermes_home, monkeypatch
):
    from agent import skill_commands
    from tools import skills_tool

    skill_dir = _make_skill(hermes_home)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: todo-skill\ndescription: Test skill\n---\n"
        "Run ${HERMES_SKILL_DIR}/scripts/todo\n"
        "uv run python3 SKILL_DIR/scripts/fetch_transcript.py\n"
    )
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", hermes_home / "skills")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    loaded = skill_commands._load_skill_payload("todo-skill", task_id="test-task")
    assert loaded is not None
    loaded_skill, loaded_skill_dir, _ = loaded
    assert loaded_skill["skill_dir"] == "/root/.hermes/skills/todo-skill"
    assert loaded_skill["host_skill_dir"] == str(skill_dir)
    assert loaded_skill_dir == skill_dir

    msg = skill_commands._build_skill_message(
        loaded_skill, loaded_skill_dir, activation_note="[activation]", session_id="test-task"
    )

    assert str(skill_dir) not in msg
    assert "[Skill directory: /root/.hermes/skills/todo-skill]" in msg
    assert "/root/.hermes/skills/todo-skill/scripts/todo" in msg
    assert "/root/.hermes/skills/todo-skill/scripts/fetch_transcript.py" in msg


def test_all_local_skill_views_hide_host_skills_path(monkeypatch):
    from tools import skills_tool

    skills_root = Path("/Users/minihome/.hermes/skills")
    if not skills_root.is_dir():
        pytest.skip("local Hermes skills root not present")

    monkeypatch.setenv("HERMES_HOME", "/Users/minihome/.hermes")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", skills_root)
    monkeypatch.setattr(
        "agent.skill_path_mapping._active_terminal_env", lambda task_id: None
    )

    leaked: list[str] = []
    checked = 0
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        if skill_md.is_symlink():
            continue
        try:
            name = str(skill_md.relative_to(skills_root).parent)
        except ValueError:
            continue
        if not name or name == ".":
            continue

        payload = json.loads(skills_tool.skill_view(name, task_id="test-task"))
        if not payload.get("success"):
            continue
        checked += 1
        text = json.dumps(payload, ensure_ascii=False)
        if "/Users/minihome/.hermes/skills/" in text:
            leaked.append(name)
        assert str(payload.get("skill_dir", "")).startswith("/root/.hermes/skills/")
        assert "host_skill_dir" not in payload

    assert checked > 0
    assert leaked == []
