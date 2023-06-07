from time import time
from typing import Dict, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class Cache(Generic[K, V]):
    def __init__(self, ttl=60):
        self._ttl = ttl
        self._data: Dict[K, V] = {}
        self._expiration: Dict[K, float] = {}

    def reset_ttl(self, key: K) -> bool:
        if key not in self._data:
            return False
        self._expiration[key] = time() + self._ttl
        return True

    def get(self, key: K, default_value: V | None = None) -> V | None:
        return self._data.get(key, default_value)

    def pop(self, key: K, default_value: V | None = None) -> V | None:
        value = self._data.pop(key, default_value)  # type: ignore
        self._expiration.pop(key, None)
        return value

    def set(self, key: K, value: V) -> None:
        self.clean()

        if key in self._data:
            self.pop(key, None)

        self._data[key] = value
        self._expiration[key] = time() + self._ttl

    def clean(self) -> None:
        expired_keys = []
        for key, expiration_time in self._expiration.items():
            if time() > expiration_time:
                expired_keys.append(key)
            break

        for key in expired_keys:
            self._data.pop(key, None)  # type: ignore
            self._expiration.pop(key, None)
