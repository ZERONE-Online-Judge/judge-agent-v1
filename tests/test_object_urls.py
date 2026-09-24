from app.object_urls import resolve_object_url


BASE = "http://10.10.10.110:6001/api"


def test_signed_minio_download_preserves_encoded_path_and_signature():
    path = "/minio/zerone/a%20b.zip?X-Amz-Credential=a%2Fb&X-Amz-Signature=abc"
    assert resolve_object_url("https://zoj.kr" + path, BASE, "https://zoj.kr") == (
        "http://10.10.10.110:6001" + path
    )


def test_unconfigured_and_unrelated_downloads_are_unchanged():
    url = "https://zoj.kr/minio/zerone/file"
    assert resolve_object_url(url, BASE) == url
    for other in ["https://other.example/minio/file", "https://zoj.kr/other/file"]:
        assert resolve_object_url(other, BASE, "https://zoj.kr") == other


def test_relative_download_uses_internal_api_origin():
    assert resolve_object_url("/api/storage/file?signature=abc", BASE) == (
        "http://10.10.10.110:6001/api/storage/file?signature=abc"
    )


def test_https_internal_download_keeps_tls_and_signed_query():
    url = "https://zoj.kr/minio/zerone/file?X-Amz-Signature=a%2Fb"
    assert resolve_object_url(url, "https://10.10.10.110:6443/api", "https://zoj.kr") == (
        "https://10.10.10.110:6443/minio/zerone/file?X-Amz-Signature=a%2Fb"
    )
