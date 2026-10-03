"""HTTP API for the editor (optional ``culture-rules[server]`` extra).

Importing this package never imports FastAPI or uvicorn: ``serve`` imports them lazily, and
``app`` (the only module that needs them) is imported by ``serve`` and by tests. The API is
stateless: every request is answered from the StoragePort, so any number of instances can
run active-active against one store. ``api/openapi.json`` is the committed contract.
"""
