from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from contextlib import contextmanager
from typing import Iterator

import duckdb


class DuckDbConnectionProvider(ABC):
    """Provide DuckDB connections behind an abstraction."""

    @abstractmethod
    def connect(self) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        """Yield a DuckDB connection."""


class InMemoryDuckDbConnectionProvider(DuckDbConnectionProvider):
    """Provide in-memory DuckDB connections."""

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with duckdb.connect(database=":memory:") as connection:
            yield connection
