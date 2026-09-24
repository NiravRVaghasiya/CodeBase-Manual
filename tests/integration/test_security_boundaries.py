"""Acceptance test for Phase 5 (security): secret-shaped content never reaches
the AI provider or the database when a repository contains a `.env` file, an
RSA private key, and a `node_modules/` tree.
"""

from __future__ import annotations

from pathlib import Path

from codebase_manual.ai.qa import answer_question
from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.domain.relationships import build_relationships
from codebase_manual.persistence.database import create_database_engine
from codebase_manual.persistence.store import IndexStore, repository_identity
from codebase_manual.repository.scanner import RepositoryScanner

_SECRET_MARKER = "AKIA-SUPER-SECRET-ACCESS-KEY-VALUE"
_RSA_MARKER = "MIIEpQIBAAKCAQEA-FAKE-RSA-KEY-MATERIAL"


class _RecordingProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def model_identifier(self) -> str:
        return "stub-model"

    def complete(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return '{"answer": "Authentication is handled here.", "cited_ids": []}'


def _build_repository(root: Path) -> None:
    (root / ".env").write_text(f"AWS_SECRET_ACCESS_KEY={_SECRET_MARKER}\n", encoding="utf-8")
    (root / "id_rsa.pem").write_text(
        f"-----BEGIN RSA PRIVATE KEY-----\n{_RSA_MARKER}\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    node_modules = root / "node_modules" / "leftpad"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text(f"// {_SECRET_MARKER}\n", encoding="utf-8")

    (root / "auth.py").write_text(
        '"""Authentication service."""\n\n\ndef login(username: str) -> bool:\n'
        '    """Authenticate a user."""\n    return True\n',
        encoding="utf-8",
    )


def test_secrets_never_reach_the_ai_prompt_or_the_database(tmp_path: Path) -> None:
    _build_repository(tmp_path)

    scanner = RepositoryScanner(tmp_path)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    relationships = build_relationships(modules)

    file_paths = {f.path for f in scan_result.files}
    assert "id_rsa.pem" not in file_paths
    assert "node_modules/leftpad/index.js" not in file_paths
    env_record = next(f for f in scan_result.files if f.path == ".env")
    assert env_record.content_hash is None

    engine = create_database_engine(f"sqlite:///{(tmp_path / 'index.db').as_posix()}")
    store = IndexStore(engine)
    store.save(scan_result, modules, relationships)

    db_bytes = (tmp_path / "index.db").read_bytes()
    assert _SECRET_MARKER.encode() not in db_bytes
    assert _RSA_MARKER.encode() not in db_bytes

    snapshot = store.latest_snapshot(
        repository_identity(scan_result), working_copy_root=str(scanner.root)
    )
    assert snapshot is not None

    provider = _RecordingProvider()
    answer = answer_question("how does authentication work", snapshot, provider)

    assert provider.prompts
    for prompt in provider.prompts:
        assert _SECRET_MARKER not in prompt
        assert _RSA_MARKER not in prompt
    assert _SECRET_MARKER not in answer.text
