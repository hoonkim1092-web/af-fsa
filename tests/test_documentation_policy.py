import importlib


def test_ensure_documentation_files_creates_korean_skeletons(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")

    from core.documentation_policy import ensure_documentation_files

    result = ensure_documentation_files(str(tmp_path))

    architecture_path = tmp_path / "docs" / "architecture.md"
    history_path = tmp_path / "docs" / "change_history.md"

    assert architecture_path.exists()
    assert history_path.exists()
    assert result["architecture_path"] == str(architecture_path)
    assert result["change_history_path"] == str(history_path)
    architecture_text = architecture_path.read_text(encoding="utf-8")
    history_text = history_path.read_text(encoding="utf-8")

    assert "# 아키텍처" in architecture_text
    assert "## 문서 규칙" in architecture_text
    assert "`ko-KR`" in architecture_text
    assert "# 변경 이력" in history_text
    assert "## 이력" in history_text


def test_documentation_policy_falls_back_to_english_templates(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "en_US.UTF-8")

    from core.documentation_policy import ensure_documentation_files, get_document_language_code

    result = ensure_documentation_files(str(tmp_path))

    assert get_document_language_code() == "en-US"
    assert "# Architecture" in (tmp_path / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "# Change History" in (tmp_path / "docs" / "change_history.md").read_text(encoding="utf-8")
    assert result["architecture_path"] == str(tmp_path / "docs" / "architecture.md")


def test_inject_documentation_contract_is_idempotent(monkeypatch):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")

    from core.documentation_policy import inject_documentation_contract

    base = "system prompt"
    combined = inject_documentation_contract(base)

    assert "system prompt" in combined
    assert "docs/architecture.md" in combined
    assert "docs/change_history.md" in combined
    assert "ko-KR" in combined
    assert "한국어" in combined
    assert inject_documentation_contract(combined) == combined


def test_project_pipeline_todo_includes_documentation_tasks(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")

    import core.config_paths

    importlib.reload(core.config_paths)

    from core.project_pipeline import ProjectPipeline

    pipeline = ProjectPipeline.__new__(ProjectPipeline)
    todo_path = pipeline._write_todo(str(tmp_path), {"todo_items": ["Implement starter workflow"]})
    content = (tmp_path / ".todo.md").read_text(encoding="utf-8")

    assert todo_path == str(tmp_path / ".todo.md")
    assert "- [ ] Implement starter workflow" in content
    assert "docs/architecture.md" in content
    assert "docs/change_history.md" in content
    assert "# 프로젝트 TODO" in content
    assert "운영체제 언어 코드 `ko-KR`" in content
