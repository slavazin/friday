"""Named registries for interchangeable algorithms. `run.yml` selects an implementation by key, so swapping
an algorithm is a one-line config change plus a registered class, never an edit at the call site."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class UnknownImplementation(KeyError):
    pass


class Registry[T]:
    def __init__(self, port: str) -> None:
        self.port = port
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        def deco(factory: Callable[..., T]) -> Callable[..., T]:
            if name in self._factories:
                raise ValueError(f"{self.port}: {name!r} registered twice")
            self._factories[name] = factory
            return factory

        return deco

    def build(self, name: str, **params: Any) -> T:
        """Returns a new implementation registered under `name`, built with `params`."""
        try:
            factory = self._factories[name]
        except KeyError:
            raise UnknownImplementation(f"{self.port}: no implementation {name!r}; have {self.names()}") from None
        return factory(**params)

    def names(self) -> list[str]:
        return sorted(self._factories)
