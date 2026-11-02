"""Offline size limits for Qzone downloads and vision model input."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
PICTURE = "https://example.test/photo.png"
CHUNK_SIZE = 64 * 1024
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a92kAAAAASUVORK5CYII="
)
PNG_BYTES = base64.b64decode(PNG_BASE64)


def run(awaitable):
    return asyncio.run(awaitable)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, error=None):
        self.chunks = chunks
        self.error = error
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


@pytest.fixture
def download(monkeypatch):
    module = load_module("maizone_image_limit_qzone", "qzone_api.py")
    state = SimpleNamespace(
        module=module,
        response=None,
        error=None,
        requests=[],
    )

    def handle(request):
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return state.response

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    state.api = module.QzoneAPI({"uin": "o00111111", "p_skey": "test-key"})
    return state


@pytest.fixture
def vision_module():
    return load_module("maizone_image_limit_vision", "vision.py")


def set_limit(monkeypatch, module, size):
    monkeypatch.setattr(module, "MAX_IMAGE_BYTES", size, raising=False)


def model():
    return SimpleNamespace(generate=AsyncMock(return_value={"success": True, "response": "有效描述"}))


def test_download_and_vision_share_the_default_size_policy(download, vision_module):
    assert download.module.MAX_IMAGE_BYTES == vision_module.MAX_IMAGE_BYTES == 10 * 1024 * 1024


def test_large_content_length_rejects_without_reading_body(download, monkeypatch):
    set_limit(monkeypatch, download.module, 8)
    stream = RecordingStream([b"unread-body"])
    download.response = httpx.Response(200, headers={"Content-Length": "9"}, stream=stream)

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None
    assert stream.chunks_read == 0
    assert stream.closed


@pytest.mark.parametrize(
    "headers",
    [{}, {"Content-Length": "1"}, {"Content-Length": "not-a-number"}, {"Content-Length": "-1"}],
    ids=["missing", "underreported", "malformed", "negative"],
)
def test_stream_limit_stops_early_even_when_headers_are_unreliable(download, monkeypatch, headers):
    set_limit(monkeypatch, download.module, CHUNK_SIZE)
    stream = RecordingStream([b"a" * CHUNK_SIZE, b"b" * CHUNK_SIZE, b"must-not-be-read"])
    download.response = httpx.Response(200, headers=headers, stream=stream)

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None
    assert stream.chunks_read == 2
    assert stream.closed


@pytest.mark.parametrize("advertised_length", [False, True])
def test_exact_download_limit_remains_usable(download, monkeypatch, advertised_length):
    set_limit(monkeypatch, download.module, len(PNG_BYTES))
    stream = RecordingStream([PNG_BYTES[:15], PNG_BYTES[15:]])
    headers = {"Content-Length": str(len(PNG_BYTES))} if advertised_length else {}
    download.response = httpx.Response(200, headers=headers, stream=stream)

    encoded = run(download.api.get_image_base64_by_url(PICTURE))

    assert base64.b64decode(encoded, validate=True) == PNG_BYTES
    assert stream.closed


def test_empty_download_is_not_returned_as_an_image(download):
    stream = RecordingStream([])
    download.response = httpx.Response(200, stream=stream)

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None
    assert stream.closed


@pytest.mark.parametrize("status", [403, 404, 500])
def test_http_failures_close_without_consuming_the_error_body(download, status):
    stream = RecordingStream([b"error page"])
    download.response = httpx.Response(status, stream=stream)

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None
    assert stream.chunks_read == 0
    assert stream.closed


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failures_return_no_image(download, error_type):
    download.error = error_type("test failure")

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None


def test_interrupted_stream_closes_and_returns_no_partial_image(download):
    stream = RecordingStream([b"partial"], error=httpx.ReadError("connection lost"))
    download.response = httpx.Response(200, stream=stream)

    assert run(download.api.get_image_base64_by_url(PICTURE)) is None
    assert stream.closed


def test_download_entry_accepts_protocol_relative_url(download):
    stream = RecordingStream([PNG_BYTES])
    download.response = httpx.Response(200, stream=stream)

    encoded = run(download.api.get_image_base64_by_url("//example.test/photo.png"))

    assert base64.b64decode(encoded, validate=True) == PNG_BYTES
    assert str(download.requests[0].url) == PICTURE
    assert stream.closed


def test_oversized_base64_is_rejected_before_decoding(vision_module, monkeypatch):
    limit = 8
    set_limit(monkeypatch, vision_module, limit)
    decoder = Mock(side_effect=AssertionError("oversized input reached the decoder"))
    monkeypatch.setattr(vision_module.base64, "b64decode", decoder)
    llm = model()
    manager = vision_module.SDKImageManager(llm)
    encoded = "A" * ((((limit + 2) // 3) * 4) + 4)

    with pytest.raises(ValueError):
        run(manager.get_image_description(encoded))

    decoder.assert_not_called()
    llm.generate.assert_not_awaited()


def test_decoded_size_is_checked_after_base64_padding_boundary(vision_module, monkeypatch):
    # Eight and nine bytes both occupy twelve Base64 characters.
    set_limit(monkeypatch, vision_module, 8)
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nx").decode("ascii")
    decoder = Mock(wraps=vision_module.base64.b64decode)
    monkeypatch.setattr(vision_module.base64, "b64decode", decoder)
    llm = model()
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(ValueError):
        run(manager.get_image_description(encoded))

    decoder.assert_called_once()
    llm.generate.assert_not_awaited()


def test_exact_vision_limit_still_sends_the_complete_image(vision_module, monkeypatch):
    set_limit(monkeypatch, vision_module, len(PNG_BYTES))
    llm = model()
    manager = vision_module.SDKImageManager(llm)

    assert run(manager.get_image_description(PNG_BASE64)) == "[图片描述：有效描述]"
    content = llm.generate.await_args.kwargs["prompt"][0]["content"]
    image = next(part for part in content if part["type"] == "image")
    assert base64.b64decode(image["image_base64"], validate=True) == PNG_BYTES
