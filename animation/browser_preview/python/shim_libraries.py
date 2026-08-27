"""Minimal package marker used as ``animation.libraries`` in Pyodide.

The browser bundle includes only the two unchanged library modules needed by
its supported plugins.  Keeping this package initializer deliberately empty
prevents the host package's convenience imports from pulling in unrelated
renderers and Pillow-backed helpers.
"""
