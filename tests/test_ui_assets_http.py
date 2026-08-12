"""HTTP contract for the served browser document and its cacheable assets.

The page used to be one uncacheable ~700 KB response whose Content-Security-Policy had to
allow ``script-src 'unsafe-inline'`` because every payload was embedded in it. It is now a
small nonced document plus content-addressed assets that can be cached forever.
"""

from __future__ import annotations

import gzip
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.server import _index_html, create_app
from app.ui_registry import ASSET_PREFIX, active_capabilities, asset_manifest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui-assets")
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_dir=root / "models",
        cache_dir=root / "cache",
        benchmark_results_file=root / "benchmarks.json",
        force_mock=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_document_is_much_smaller_than_the_inline_composition(client):
    response = client.get("/")
    assert response.status_code == 200
    # The payloads moved out of the document into cacheable assets.
    assert len(response.text) < len(_index_html()) / 2


def test_document_policy_forbids_inline_script_without_a_nonce(client):
    response = client.get("/")
    policy = response.headers["content-security-policy"]
    match = re.search(r"script-src 'self' 'nonce-([^']+)'", policy)
    assert match, policy
    assert "'unsafe-inline'" not in policy.split("style-src")[0]
    # Every inline block in the body carries this response's nonce.
    nonce = match.group(1)
    for tag in re.finditer(r"<script(?![^>]*\bsrc\s*=)[^>]*>", response.text):
        assert f'nonce="{nonce}"' in tag.group(0), tag.group(0)


def test_each_response_gets_a_fresh_nonce(client):
    first = re.search(r"'nonce-([^']+)'", client.get("/").headers["content-security-policy"])
    second = re.search(r"'nonce-([^']+)'", client.get("/").headers["content-security-policy"])
    assert first and second and first.group(1) != second.group(1)
    assert client.get("/").headers["cache-control"] == "no-store, must-revalidate"


def test_document_references_assets_that_are_all_served(client):
    document = client.get("/").text
    referenced = set(re.findall(r'(?:src|href)="(/ui/[^"]+)"', document))
    assert referenced, "the document should reference composed assets"
    for url in referenced:
        response = client.get(url)
        assert response.status_code == 200, url


def test_assets_are_cached_immutably(client):
    url = next(iter(asset_manifest(active_capabilities())))
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_assets_negotiate_gzip_without_a_streaming_middleware(client):
    """Compression is precomputed per asset rather than applied to every response.

    A global compression middleware would also buffer the Server-Sent Events used for chat
    streaming, delaying tokens.
    """

    url = next(url for url in asset_manifest(active_capabilities()) if url.endswith(".js"))
    plain = client.get(url, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in {key.lower() for key in plain.headers}

    compressed = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert compressed.status_code == 200
    # TestClient transparently decodes, so compare decoded content.
    assert compressed.text == plain.text


def test_asset_bytes_match_the_composed_payload(client):
    manifest = asset_manifest(active_capabilities())
    url, asset = next(iter(manifest.items()))
    assert client.get(url, headers={"Accept-Encoding": "identity"}).content == asset.body
    assert gzip.decompress(asset.gzip_body) == asset.body


def test_unknown_asset_is_not_a_filesystem_lookup(client):
    for candidate in (
        "does-not-exist.0123456789abcdef.js",
        "..%2F..%2Fweb%2Findex.html",
        "....//....//etc/passwd",
    ):
        response = client.get(ASSET_PREFIX + candidate)
        assert response.status_code in {404, 400}, candidate
        assert "root:" not in response.text
        assert "<!DOCTYPE" not in response.text.upper()
