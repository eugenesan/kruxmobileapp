"""A small, file-backed stand-in for kivy.storage.jsonstore.JsonStore.

Only the surface KruxMobileApp's AndroidStore touches is implemented. Values
are held in a JSON file named by the constructor's ``filename``.

The store caches its data, the way Kivy's does. That detail matters:
KruxMobileApp's AndroidStore writes a key with ``self.settings[ns][key] = value``
and then assigns ``self.settings[ns] = self.settings[ns]`` to force the write
out. If this stub reloaded from disk on every read, the right hand side of that
assignment would be a *different* dict parsed from the file -- without the key
just set -- and the write would be silently lost. Caching keeps the two sides
identical, which is what makes that idiom work.
"""
import json
import os


class JsonStore:
    """Subset of Kivy's JsonStore: a dict of namespaces, persisted as JSON."""

    def __init__(self, filename, indent=None, binary=False):
        # Kivy accepts and ignores these; take them so the call site is honest.
        del indent, binary
        self.filename = filename
        self._data = {}
        self.reload()

    def reload(self):
        """Re-read the backing file, as Kivy's store does when opened."""
        try:
            with open(self.filename, "r", encoding="utf8") as f:
                loaded = json.load(f)
            self._data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self._data = {}

    def _flush(self):
        with open(self.filename, "w", encoding="utf8") as f:
            json.dump(self._data, f)

    def exists(self, namespace):
        return namespace in self._data

    def keys(self):
        return list(self._data.keys())

    def get(self, namespace, key=None, default_value=None):
        if namespace not in self._data:
            return default_value
        if key is None:
            return self._data[namespace]
        return self._data[namespace].get(key, default_value)

    def getdefaults(self, namespace, defaults):
        self._data.setdefault(namespace, {})
        for k, v in defaults.items():
            self._data[namespace].setdefault(k, v)
        self._flush()
        return self._data[namespace]

    def put(self, namespace, data=None, **kwargs):
        merged = dict(data or {})
        merged.update(kwargs)
        self._data[namespace] = merged
        self._flush()

    def delete(self, namespace):
        self._data.pop(namespace, None)
        self._flush()

    def clear(self):
        self._data = {}
        self._flush()

    def __getitem__(self, namespace):
        return self._data[namespace]

    def __setitem__(self, namespace, data):
        self._data[namespace] = data
        self._flush()

    def __contains__(self, namespace):
        return namespace in self._data

    def __iter__(self):
        return iter(self._data)

    def sync(self):
        self._flush()

    def __repr__(self):
        return "JsonStore(%r, keys=%r)" % (
            os.path.basename(self.filename),
            self.keys(),
        )
