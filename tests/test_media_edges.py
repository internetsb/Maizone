"""Offline regressions for usable video context and media URL normalization."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from test_qzone_api import (
    AUTHOR,
    OUTER_TID,
    PICTURE,
    THUMBNAIL,
    VIDEO,
    desktop_feed,
    message,
    run,
)
from test_qzone_api import harness as harness


ROOT = Path(__file__).resolve().parents[1]
RELATIVE_PICTURE = PICTURE.removeprefix("https:")
RELATIVE_THUMBNAIL = THUMBNAIL.removeprefix("https:")
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a92kAAAAASUVORK5CYII="
)


def video_feed(source, *, text="", quote=None, cover=THUMBNAIL):
    if source == "html":
        feed = desktop_feed(text=text, quote=quote)
        image = f'<div class="video-img"><img src="{cover}"></div>' if cover else ""
        feed["html"] += f'<div class="img-box f-video-wrap play" url3="{VIDEO}">{image}</div>'
    else:
        feed = message(content=text, quote=quote)
        feed["video"] = [{"url3": VIDEO}]
        if cover:
            feed["video"][0]["url1"] = cover
    return feed


def read_with_other_feed(harness, source, feed):
    if source == "html":
        harness.feeds = [feed, desktop_feed(tid="readable-feed", text="readable text")]
        return run(harness.api.get_qzone_list())
    harness.messages = [feed, message(tid="readable-feed", content="readable text")]
    return run(harness.api.get_list(AUTHOR, 5, False))


@pytest.mark.parametrize("source", ["html", "messages"])
@pytest.mark.parametrize("failure", ["no-cover", "download", "description"])
def test_video_without_readable_context_is_skipped(harness, monkeypatch, source, failure):
    feed = video_feed(source, cover=None if failure == "no-cover" else THUMBNAIL)

    async def failed_download(url):
        harness.images.append(url)
        return None

    async def failed_description(encoded):
        raise RuntimeError("vision unavailable")

    if failure == "download":
        monkeypatch.setattr(harness.api, "get_image_base64_by_url", failed_download)
    elif failure == "description":
        monkeypatch.setattr(harness.module.image_manager, "get_image_description", failed_description)

    feeds = read_with_other_feed(harness, source, feed)

    assert [item["tid"] for item in feeds] == ["readable-feed"]
    if failure == "no-cover":
        assert not harness.images


@pytest.mark.parametrize("source", ["html", "messages"])
@pytest.mark.parametrize("context", ["caption", "quote", "cover"])
def test_video_with_readable_context_keeps_playback_url(harness, monkeypatch, source, context):
    feed = video_feed(
        source,
        text="video caption" if context == "caption" else "",
        quote="quoted text" if context == "quote" else None,
    )
    if context != "cover":
        async def failed_description(encoded):
            raise RuntimeError("vision unavailable")

        monkeypatch.setattr(harness.module.image_manager, "get_image_description", failed_description)

    feeds = read_with_other_feed(harness, source, feed)

    assert [item["tid"] for item in feeds] == [OUTER_TID, "readable-feed"]
    result = feeds[0]
    assert result["videos"] == [VIDEO]
    assert result["content"] == ("video caption" if context == "caption" else "")
    assert result["rt_con"] == ("quoted text" if context == "quote" else "")
    assert result["images"] == ([f"image:{THUMBNAIL}"] if context == "cover" else [])


@pytest.mark.parametrize("source", ["html", "messages"])
def test_captioned_video_without_cover_is_still_readable(harness, source):
    feeds = read_with_other_feed(harness, source, video_feed(source, text="video caption", cover=None))

    assert [item["tid"] for item in feeds] == [OUTER_TID, "readable-feed"]
    assert feeds[0]["videos"] == [VIDEO]
    assert feeds[0]["images"] == []
    assert not harness.images


def test_html_protocol_relative_photos_are_normalized_before_deduplication(harness):
    feed = desktop_feed(text="")
    feed["html"] += (
        '<div class="img-box">'
        f'<img src="{RELATIVE_PICTURE}"><img src="{PICTURE}">'
        '</div>'
    )
    harness.feeds = [feed]

    result = run(harness.api.get_qzone_list())[0]

    assert harness.images == [PICTURE]
    assert result["images"] == [f"image:{PICTURE}"]


def test_html_protocol_relative_video_cover_is_normalized_before_deduplication(harness):
    feed = desktop_feed(text="")
    feed["html"] += (
        f'<div class="img-box f-video-wrap play" url3="{VIDEO}">'
        f'<div class="video-img"><img src="{RELATIVE_THUMBNAIL}"></div>'
        f'<img src="{THUMBNAIL}">'
        '</div>'
    )
    harness.feeds = [feed]

    result = run(harness.api.get_qzone_list())[0]

    assert harness.images == [THUMBNAIL]
    assert result["images"] == [f"image:{THUMBNAIL}"]
    assert result["videos"] == [VIDEO]


def test_message_protocol_relative_images_remain_normalized_and_deduplicated(harness):
    feed = message()
    feed["pic"] = [{"url1": RELATIVE_PICTURE}, {"pic_id": PICTURE}]
    feed["video"] = [{"url1": RELATIVE_THUMBNAIL, "url3": VIDEO}]
    harness.messages = [feed]

    result = run(harness.api.get_list(AUTHOR, 5, False))[0]

    assert harness.images == [PICTURE, THUMBNAIL]
    assert result["images"] == [f"image:{PICTURE}", f"image:{THUMBNAIL}"]
    assert result["videos"] == [VIDEO]


@pytest.fixture
def image_download_api(monkeypatch):
    spec = importlib.util.spec_from_file_location("maizone_media_download_under_test", ROOT / "qzone_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=PNG_BYTES)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    api = module.QzoneAPI({"uin": "o00111111", "p_skey": "test-key"})
    return SimpleNamespace(api=api, requests=requests)


@pytest.mark.parametrize("url", [PICTURE, RELATIVE_PICTURE])
def test_download_entry_accepts_absolute_or_protocol_relative_url(image_download_api, url):
    result = run(image_download_api.api.get_image_base64_by_url(url))

    assert base64.b64decode(result, validate=True) == PNG_BYTES
    assert len(image_download_api.requests) == 1
    assert str(image_download_api.requests[0].url) == PICTURE
