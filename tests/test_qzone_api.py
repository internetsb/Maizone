"""Offline regression tests for Qzone quoted feeds and detail recovery."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest


PLACEHOLDER = "功能内测中，内容暂不支持查看。"
AUTHOR = "222222"
VIEWER = "111111"
OUTER_TID = "outer-feed"
PICTURE = "https://example.test/quoted.jpg"
THUMBNAIL = "https://example.test/video.jpg"
VIDEO = "https://example.test/video.mp4"


def run(awaitable):
    return asyncio.run(awaitable)


def desktop_feed(tid=OUTER_TID, text=None, *, quote=None):
    text_html = f'<div class="f-info">{text}</div>' if text is not None else ""
    quote_html = (
        '<div class="txt-box" data-orig-uin="333333" '
        f'data-orig-tid="original-feed">{quote}</div>'
        if quote is not None
        else ""
    )
    return {
        "appid": 311,
        "uin": AUTHOR,
        "key": tid,
        "feedstime": "今天12:26",
        "html": text_html
        + quote_html
        + '<li class="comments-item bor3" data-uin="444444" '
        'data-tid="7" data-nick="外层评论者">'
        '<div class="comments-content">外层评论'
        '<div class="comments-op">回复</div></div>'
        '<span class="state">12:42</span></li>',
    }


def message(tid=OUTER_TID, content="", *, quote=None):
    result = {
        "tid": tid,
        "content": content,
        "createTime": "2026年10月29日 12:26",
        "commentlist": [
            {
                "uin": "444444",
                "name": "外层评论者",
                "tid": "7",
                "content": "外层评论",
                "createTime": "12:42",
            }
        ],
    }
    if quote is not None:
        result["rt_con"] = {"content": quote}
    return result


def detail(**overrides):
    result = {
        "code": 0,
        "uin": AUTHOR,
        "tid": OUTER_TID,
        "content": "",
        "rt_con": {"content": "引用原文", "uin": "333333", "tid": "original-feed"},
        "pic": [{"url1": PICTURE}],
        "commentlist": [{"uin": "555555", "tid": "99", "content": "详情评论"}],
    }
    result.update(overrides)
    return result


@pytest.fixture
def harness(monkeypatch):
    # Import this standalone API module without loading the host bot or its secrets.
    path = Path(__file__).resolve().parents[1] / "qzone_api.py"
    if os.environ.get("MAIZONE_TEST_BASELINE") == "1":
        source = subprocess.run(
            ["git", "show", "HEAD:qzone_api.py"],
            cwd=path.parent,
            check=True,
            capture_output=True,
            encoding="utf-8",
        ).stdout
        module = ModuleType("maizone_qzone_api_under_test")
        module.__file__ = str(path)
        exec(compile(source, str(path), "exec"), module.__dict__)
    else:
        spec = importlib.util.spec_from_file_location("maizone_qzone_api_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    state = SimpleNamespace(
        module=module,
        feeds=[],
        messages=[],
        detail_payload=detail(),
        qzone_payload=None,
        list_payload=None,
        requests=[],
        images=[],
    )

    def response(payload):
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, httpx.Response):
            return payload
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    def handle(request):
        state.requests.append(request)
        if request.url.path.endswith("emotion_cgi_msgdetail_v6"):
            return response(state.detail_payload)
        if request.url.path.endswith("feeds3_html_more"):
            payload = state.qzone_payload
            if payload is None:
                payload = {"code": 0, "data": {"data": state.feeds}}
            return response(payload)
        if request.url.path.endswith("emotion_cgi_msglist_v6"):
            payload = state.list_payload
            if payload is None:
                payload = {
                    "code": 0,
                    "logininfo": {"name": "机器人"},
                    "msglist": state.messages,
                }
            return response(payload)
        raise AssertionError(f"Unexpected HTTP request: {request.url}")

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)

    async def get_image(url):
        state.images.append(url)
        return url

    async def describe_image(encoded):
        return f"image:{encoded}"

    state.api = module.QzoneAPI({"uin": f"o00{VIEWER}", "p_skey": "test-key"})
    monkeypatch.setattr(state.api, "get_image_base64_by_url", get_image)
    monkeypatch.setattr(module, "image_manager", SimpleNamespace(get_image_description=describe_image))
    return state


def detail_requests(harness):
    return [
        request
        for request in harness.requests
        if request.url.path.endswith("emotion_cgi_msgdetail_v6")
    ]


def assert_outer_comment(feed):
    assert len(feed["comments"]) == 1
    assert feed["comments"][0]["qq_account"] == "444444"
    assert feed["comments"][0]["comment_tid"] == 7
    assert feed["comments"][0]["content"] == "外层评论"


def test_html_quote_placeholder_recovers_outer_feed_not_original(harness):
    harness.feeds = [desktop_feed(quote=PLACEHOLDER)]
    feeds = run(harness.api.get_qzone_list())

    assert len(feeds) == 1
    feed = feeds[0]
    assert feed["target_qq"] == AUTHOR
    assert feed["tid"] == OUTER_TID
    assert feed["created_time"] == "今天12:26"
    assert feed["content"] == ""
    assert feed["rt_con"] == "引用原文"
    assert feed["images"] == [f"image:{PICTURE}"]
    assert_outer_comment(feed)

    requests = detail_requests(harness)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.host == "h5.qzone.qq.com"
    assert request.url.params["uin"] == AUTHOR
    assert request.url.params["tid"] == OUTER_TID
    assert request.url.params["format"] == "json"
    assert request.url.params["g_tk"] == harness.api.gtk2


def test_html_main_placeholder_recovers_text_video_and_cover(harness):
    harness.feeds = [desktop_feed(text=PLACEHOLDER)]
    harness.detail_payload = detail(
        content="发布者的话",
        pic=[],
        rt_con={"content": "引用原文", "video": [{"url1": THUMBNAIL, "url3": VIDEO}]},
    )

    feed = run(harness.api.get_qzone_list())[0]
    assert feed["content"] == "发布者的话"
    assert feed["rt_con"] == "引用原文"
    assert feed["images"] == [f"image:{THUMBNAIL}"]
    assert feed["videos"] == [VIDEO]


def test_normal_html_quote_does_not_request_detail(harness):
    harness.feeds = [desktop_feed(text="发布者的话", quote="原作者：传统转发")]
    feed = run(harness.api.get_qzone_list())[0]

    assert feed["content"] == "发布者的话"
    assert feed["rt_con"] == "传统转发"
    assert_outer_comment(feed)
    assert not detail_requests(harness)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(403, text="Forbidden"),
        httpx.ReadTimeout("detail timeout"),
        {"code": -100, "message": "没有访问权限"},
        detail(uin="333333"),
        detail(tid="wrong-feed"),
        detail(content=PLACEHOLDER),
        detail(rt_con={"content": PLACEHOLDER}),
        {"code": 0, "uin": AUTHOR, "tid": OUTER_TID},
        "not json",
        [],
    ],
    ids=[
        "forbidden",
        "timeout",
        "no-permission",
        "wrong-author",
        "wrong-feed",
        "main-still-placeholder",
        "quote-still-placeholder",
        "empty-detail",
        "bad-json",
        "bad-shape",
    ],
)
def test_failed_html_recovery_skips_unreadable_feed_but_keeps_others(harness, failure):
    harness.feeds = [
        desktop_feed(quote=PLACEHOLDER),
        desktop_feed(tid="readable-feed", text="正常内容"),
    ]
    harness.detail_payload = failure

    feeds = run(harness.api.get_qzone_list())
    assert [feed["tid"] for feed in feeds] == ["readable-feed"]
    assert feeds[0]["content"] == "正常内容"


@pytest.mark.parametrize("placeholder_in_quote", [False, True])
def test_message_placeholder_recovers_content_and_preserves_comments(harness, placeholder_in_quote):
    harness.messages = [
        message(content="发布者的话", quote=PLACEHOLDER)
        if placeholder_in_quote
        else message(content=PLACEHOLDER)
    ]
    harness.detail_payload = detail(content="发布者的话")
    feeds = run(harness.api.get_list(AUTHOR, 5, False))

    assert len(feeds) == 1
    feed = feeds[0]
    assert feed["target_qq"] == AUTHOR
    assert feed["tid"] == OUTER_TID
    assert feed["created_time"] == "2026年10月29日 12:26"
    assert feed["content"] == "发布者的话"
    assert feed["rt_con"] == "引用原文"
    assert feed["images"] == [f"image:{PICTURE}"]
    assert_outer_comment(feed)
    assert len(detail_requests(harness)) == 1


def test_normal_message_reads_quoted_media_without_detail_and_deduplicates(harness):
    msg = message(content="发布者的话", quote="引用原文")
    msg["pic"] = [{"url1": PICTURE}, {"smallurl": PICTURE}]
    msg["video"] = [{"url1": THUMBNAIL, "url3": VIDEO}]
    msg["rt_con"]["pic"] = [{"pic_id": PICTURE}]
    msg["rt_con"]["video"] = [{"url1": THUMBNAIL, "url3": VIDEO}]
    harness.messages = [msg]

    feed = run(harness.api.get_list(AUTHOR, 5, False))[0]
    assert feed["rt_con"] == "引用原文"
    assert feed["images"] == [f"image:{PICTURE}", f"image:{THUMBNAIL}"]
    assert feed["videos"] == [VIDEO]
    assert harness.images == [PICTURE, THUMBNAIL]
    assert not detail_requests(harness)


def test_recovered_media_deduplicates_top_level_and_quoted_urls(harness):
    harness.feeds = [desktop_feed(quote=PLACEHOLDER)]
    harness.detail_payload = detail(
        pic=[{"url1": PICTURE}, {"smallurl": PICTURE}],
        video=[{"url1": THUMBNAIL, "url3": VIDEO}],
        rt_con={
            "content": "引用原文",
            "pic": [{"pic_id": PICTURE}],
            "video": [{"url1": THUMBNAIL, "url3": VIDEO}],
        },
    )

    feed = run(harness.api.get_qzone_list())[0]
    assert feed["images"] == [f"image:{PICTURE}", f"image:{THUMBNAIL}"]
    assert feed["videos"] == [VIDEO]
    assert harness.images == [PICTURE, THUMBNAIL]


def test_failed_message_recovery_keeps_other_messages(harness):
    harness.messages = [message(content=PLACEHOLDER), message(tid="readable-feed", content="正常内容")]
    harness.detail_payload = {"code": -100, "message": "没有访问权限"}

    feeds = run(harness.api.get_list(AUTHOR, 5, False))
    assert [feed["tid"] for feed in feeds] == ["readable-feed"]


@pytest.mark.parametrize("payload", ["not json", "null", "[]", "{}", '{"data":null}', '{"data":{"data":null}}'])
def test_bad_html_list_responses_do_not_raise(harness, payload):
    harness.qzone_payload = payload
    feeds = run(harness.api.get_qzone_list())
    assert isinstance(feeds, list)
    assert not [feed for feed in feeds if "tid" in feed]


@pytest.mark.parametrize("payload", ["not json", "null", "[]", "{}", '{"code":0,"msglist":null}'])
def test_bad_message_list_responses_do_not_raise(harness, payload):
    harness.list_payload = payload
    feeds = run(harness.api.get_list(AUTHOR, 5, False))
    assert isinstance(feeds, list)
    assert not [feed for feed in feeds if "tid" in feed]


def test_detail_rejects_non_json_callback_wrapper(harness):
    harness.detail_payload = "_preloadCallback(" + json.dumps(detail(), ensure_ascii=False) + ");"
    recovered = run(harness.api._get_emotion_detail(AUTHOR, OUTER_TID))
    assert recovered is None


def test_detail_accepts_image_only_quote(harness):
    harness.detail_payload = detail(rt_con={"content": ""})
    recovered = run(harness.api._get_emotion_detail(AUTHOR, OUTER_TID))
    assert recovered["pic"] == [{"url1": PICTURE}]


def test_detail_rejects_non_text_content(harness):
    harness.detail_payload = detail(content={"unexpected": "field"})
    assert run(harness.api._get_emotion_detail(AUTHOR, OUTER_TID)) is None


@pytest.mark.parametrize("source", ["html", "messages"])
@pytest.mark.parametrize("failure", ["download", "description"])
def test_image_only_recovery_skips_feed_when_images_cannot_be_read(harness, monkeypatch, source, failure):
    harness.detail_payload = detail(rt_con={"content": ""})

    async def download_failure(url):
        return None

    async def description_failure(encoded):
        raise ValueError("description unavailable")

    if failure == "download":
        monkeypatch.setattr(harness.api, "get_image_base64_by_url", download_failure)
    else:
        monkeypatch.setattr(harness.module.image_manager, "get_image_description", description_failure)

    if source == "html":
        harness.feeds = [
            desktop_feed(quote=PLACEHOLDER),
            desktop_feed(tid="readable-feed", text="正常内容"),
        ]
        feeds = run(harness.api.get_qzone_list())
    else:
        harness.messages = [message(content=PLACEHOLDER), message(tid="readable-feed", content="正常内容")]
        feeds = run(harness.api.get_list(AUTHOR, 5, False))

    assert [feed["tid"] for feed in feeds] == ["readable-feed"]


@pytest.mark.parametrize("source", ["html", "messages"])
@pytest.mark.parametrize("missing_field", ["content", "rt_con"])
def test_partial_detail_preserves_known_outer_text_or_quote(harness, source, missing_field):
    if missing_field == "content":
        text, quote = "已知发布者的话", PLACEHOLDER
        expected_text, expected_quote = text, "引用原文"
    else:
        text, quote = PLACEHOLDER, "已知引用原文"
        expected_text, expected_quote = "恢复的正文", quote
    harness.detail_payload = detail(content="恢复的正文")
    del harness.detail_payload[missing_field]

    if source == "html":
        harness.feeds = [desktop_feed(text=text, quote=quote)]
        feed = run(harness.api.get_qzone_list())[0]
    else:
        harness.messages = [message(content=text, quote=quote)]
        feed = run(harness.api.get_list(AUTHOR, 5, False))[0]

    assert feed["content"] == expected_text
    assert feed["rt_con"] == expected_quote
    assert_outer_comment(feed)
