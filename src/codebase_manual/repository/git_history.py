"""Git history as additional evidence for change planning.

Co-change counts are historical evidence -- files that tend to change
together -- not proof of an architectural dependency. Callers should
present them as a hint, alongside (not instead of) the relationship graph.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CoChangeFact:
    path: str
    shared_commit_count: int


def co_changed_files(
    root: Path,
    path: str,
    *,
    max_commits: int = 500,
    min_shared_commits: int = 2,
) -> list[CoChangeFact]:
    """Files that changed together with `path` in at least `min_shared_commits` commits.

    Returns an empty list (rather than raising) whenever history isn't
    available: no Git, no commits touching `path`, or any other repository
    state that makes history unreadable.
    """
    try:
        import git
    except ImportError:
        return []

    try:
        repo = git.Repo(root, search_parent_directories=False)
        commits = list(repo.iter_commits(paths=path, max_count=max_commits))
    except Exception:  # noqa: BLE001 - many distinct Git/filesystem failures, all mean "no history".
        return []

    counts: Counter[str] = Counter()
    for commit in commits:
        try:
            changed_paths = set(commit.stats.files.keys())
        except Exception:  # noqa: BLE001 - a single unreadable commit shouldn't abort the rest.
            continue
        changed_paths.discard(path)
        counts.update(str(p) for p in changed_paths)

    facts = [
        CoChangeFact(path=other_path, shared_commit_count=count)
        for other_path, count in counts.items()
        if count >= min_shared_commits
    ]
    facts.sort(key=lambda fact: fact.shared_commit_count, reverse=True)
    return facts
