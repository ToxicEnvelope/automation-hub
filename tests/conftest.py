from __future__ import annotations

import sys
import types


# The production image installs Azure SDK packages. The local artifact-validation
# environment does not, so provide import-only stubs for unit tests that use a
# fully in-memory BlobServiceClient fake.
azure = types.ModuleType("azure")
identity = types.ModuleType("azure.identity")
storage = types.ModuleType("azure.storage")
blob = types.ModuleType("azure.storage.blob")


class DefaultAzureCredential:  # pragma: no cover - import compatibility only
    def __init__(self, *args, **kwargs):
        pass


class BlobServiceClient:  # pragma: no cover - import compatibility only
    def __init__(self, *args, **kwargs):
        pass


identity.DefaultAzureCredential = DefaultAzureCredential
blob.BlobServiceClient = BlobServiceClient
storage.blob = blob
azure.identity = identity
azure.storage = storage

sys.modules.setdefault("azure", azure)
sys.modules.setdefault("azure.identity", identity)
sys.modules.setdefault("azure.storage", storage)
sys.modules.setdefault("azure.storage.blob", blob)
