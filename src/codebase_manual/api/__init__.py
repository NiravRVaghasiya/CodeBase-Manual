"""FastAPI web UI over an indexed repository."""

from codebase_manual.api.app import RepositoryNotIndexedError, create_app

__all__ = ["RepositoryNotIndexedError", "create_app"]
