import asyncio

import yaml

import web.api.agents as agents_api


def _write_agent(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"name": name, "role": f"{name} role"}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_project_catalog_prefers_project_copy(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    global_agent = repo_root / "agents" / "deadbyte.yaml"
    project_agent = repo_root / "projects" / "proj_a" / "agents" / "deadbyte.yaml"
    _write_agent(global_agent, "Global Deadbyte")
    _write_agent(project_agent, "Project Deadbyte")

    monkeypatch.setattr(agents_api, "_ROOT_DIR", repo_root)
    monkeypatch.setattr(agents_api, "AGENTS_DIR", repo_root / "agents")
    monkeypatch.delenv("AGENT_PROJECT_ROOT", raising=False)

    payload = asyncio.run(agents_api.list_agent_catalog(project_id="proj_a"))

    assert payload["count"] == 1
    assert payload["agents"][0]["name"] == "Project Deadbyte"


def test_patch_agent_creates_project_local_copy(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    global_agent = repo_root / "agents" / "deadbyte.yaml"
    _write_agent(global_agent, "Global Deadbyte")

    monkeypatch.setattr(agents_api, "_ROOT_DIR", repo_root)
    monkeypatch.setattr(agents_api, "AGENTS_DIR", repo_root / "agents")
    monkeypatch.delenv("AGENT_PROJECT_ROOT", raising=False)

    req = agents_api.AgentPatchRequest(updates={"name": "Local Deadbyte"})
    payload = asyncio.run(agents_api.patch_agent("deadbyte", req, project_id="proj_b"))

    project_agent = repo_root / "projects" / "proj_b" / "agents" / "deadbyte.yaml"
    assert payload["ok"] is True
    assert yaml.safe_load(global_agent.read_text(encoding="utf-8"))["name"] == "Global Deadbyte"
    assert yaml.safe_load(project_agent.read_text(encoding="utf-8"))["name"] == "Local Deadbyte"
