"""Minimal stand-in for the Kivy packages the Android build imports.

KruxMobileApp's Android modification replaces Krux's own Store with an
AndroidStore backed by kivy.storage.jsonstore.JsonStore. Krux's test suite has
no Kivy, so this provides just the slice of the API AndroidStore uses:

    JsonStore(filename)
    store.keys()            -> list of namespace names
    store.get(namespace)    -> dict, or None when absent
    store[namespace]        -> dict
    store[namespace] = d    -> writes the namespace
    store.clear()           -> empties the store

State is kept in a JSON file so the round trip the tests actually exercise --
write a setting, read it back -- behaves like the real thing rather than
silently succeeding against a dict.
"""
