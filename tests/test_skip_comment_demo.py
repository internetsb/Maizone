"""Demo: selective no-comment when reading others' QZone (pure helpers, no network)."""

from __future__ import annotations


def is_skip_comment(text) -> bool:
    if text is None:
        return True
    t = str(text).strip()
    if not t:
        return True
    for _ in range(2):
        t = t.strip().strip("\"'“”‘’`")
        if len(t) >= 2 and t[0] in "[(（【" and t[-1] in "])）】":
            t = t[1:-1].strip()
    normalized = "".join(t.split()).lower()
    return normalized in {
        "不回复", "不评论", "跳过", "无", "无评论",
        "无需回复", "不用回复", "不必回复",
        "skip", "none", "n/a", "na", "null",
    }


def parse_enable_comment(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "default", "默认"}:
        return default
    if text in {"0", "false", "no", "n", "off", "否", "不", "不回复", "不评论", "skip", "none"}:
        return False
    if text in {"1", "true", "yes", "y", "on", "是", "回复", "评论"}:
        return True
    return default


def test_skip_tokens():
    assert is_skip_comment("不回复")
    assert is_skip_comment("【不回复】")
    assert is_skip_comment(" skip ")
    assert not is_skip_comment("看起来不错")


def test_enable_comment_flag():
    assert parse_enable_comment("false") is False
    assert parse_enable_comment("不回复") is False
    assert parse_enable_comment("true") is True
    assert parse_enable_comment(None) is True


if __name__ == "__main__":
    test_skip_tokens()
    test_enable_comment_flag()
    print("demo OK: skip-comment helpers")
