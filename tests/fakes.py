from __future__ import annotations

from types import SimpleNamespace


class FakeDownload:
    def __init__(self, payload: bytes):
        self.payload = payload

    def readall(self) -> bytes:
        return self.payload


class FakeContainerClient:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def download_blob(self, name: str) -> FakeDownload:
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return FakeDownload(self.blobs[name])

    def upload_blob(self, name: str, data, overwrite: bool = False):
        if not overwrite and name in self.blobs:
            raise FileExistsError(name)
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.blobs[name] = bytes(data)

    def list_blobs(self, name_starts_with: str = "", **kwargs):
        for name in sorted(self.blobs):
            if name.startswith(name_starts_with):
                yield SimpleNamespace(name=name)


class FakeBlobServiceClient:
    def __init__(self):
        self.container = FakeContainerClient()

    def get_container_client(self, _name: str) -> FakeContainerClient:
        return self.container
