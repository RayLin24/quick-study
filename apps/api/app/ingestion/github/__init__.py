"""Reading a public GitHub repository through the API, pinned to one commit.

Nothing in this package executes anything from the repository. No script runs, no build
step, no hook, no package manager, no ``setup.py``, no ``tsconfig`` and no plugin. Files
arrive over the Tree and Blob endpoints as bytes and are treated as text to be parsed and
quoted. That is the whole contract, and it is what makes analysing a stranger's repository
safe.
"""
