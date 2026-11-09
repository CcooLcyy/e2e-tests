import pytest

from scripts import windows_release


def test_plaintext_platform_endpoint(monkeypatch):
    endpoint = "http://60.208.15.90:50000"
    monkeypatch.setenv("PLATFORM_GRPC_ENDPOINT", endpoint)
    assert windows_release.grpc_endpoint() == endpoint


@pytest.mark.parametrize("endpoint", [
    "",
    "60.208.15.90:50000",
    "https://60.208.15.90:50000",
    "http://60.208.15.90:50000/latest.json",
    "http://60.208.15.90:65536",
])
def test_rejects_invalid_platform_endpoint(monkeypatch, endpoint):
    monkeypatch.setenv("PLATFORM_GRPC_ENDPOINT", endpoint)
    with pytest.raises(windows_release.ReleaseError, match="http://host:port"):
        windows_release.grpc_endpoint()
