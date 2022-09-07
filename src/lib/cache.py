from time import time


# Python 3.7+


class Cache:
    def __init__(self, ttl=60):
        self._ttl = ttl
        self._data = {}
        self._expiration = {}

    def get(self, key, default_value=None):
        return self._data.get(key, default_value)

    def pop(self, key, *args):
        value = self._data.pop(key, *args)
        self._expiration.pop(key, None)
        return value

    def set(self, key, value):
        self.clean()

        if key in self._data:
            self.pop(key, None)

        self._data[key] = value
        self._expiration[key] = time() + self._ttl

    def clean(self):
        expired_keys = []
        for key, expiration_time in self._expiration.items():
            if time() > expiration_time:
                expired_keys.append(key)
            break

        for key in expired_keys:
            self._data.pop(key, None)
            self._expiration.pop(key, None)
