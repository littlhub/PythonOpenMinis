"""[T-guard-path-not-secret] 沙箱守卫别把「文件名里的哈希」当密钥。

用户实测：QQ 发来的图片落盘后叫
``20260918-073217-qq-dd1bf336-A39C8122FF85B3B700719B9836EA6C4D.jpg``，
这个路径随消息正文一起出站时，被「长十六进制串（疑似密钥）」那条规则命中并
遮成「已拦截·敏感信息」—— 模型于是连图片路径都看不到，表现得像
「图片被沙箱拦截了敏感信息，无法识别图片」。

收紧的口子只有两个（越小越安全）：命中片段紧跟着扩展名、或紧挨着路径分隔符。
这两条之外的十六进制串照旧判为疑似密钥。
"""

from __future__ import annotations

from openminis.sandbox.guard import redact_secrets, scan_secret_text

#: 用户那台机器上真实出现过的文件名。
QQ_IMAGE = "20260918-073217-qq-dd1bf336-A39C8122FF85B3B700719B9836EA6C4D.jpg"
QQ_PATH = f"C:/Users/loo/openminis/workspace/uploads/{QQ_IMAGE}"


def test_image_path_is_not_redacted():
    """图片引用（网页端与 IM 用的是同一种 markdown）原样保留。"""
    text = f"![{QQ_IMAGE}]({QQ_PATH})"
    out, hits = redact_secrets(text)
    assert hits == []
    assert out == text
    assert str(out).count("已拦截") == 0


def test_file_attachment_path_is_not_redacted():
    text = "[附件: A39C8122FF85B3B700719B9836EA6C4D.xlsx](C:/tmp/a.xlsx)"
    out, hits = redact_secrets(text)
    assert hits == [] and out == text


def test_hashed_dir_name_is_not_redacted():
    """目录名是哈希时也一样 —— 紧挨着路径分隔符就不算密钥。"""
    text = "看这张图 /var/minis/workspace/uploads/9f8e7d6c5b4a39281706f5e4d3c2b1a0.png"
    assert scan_secret_text(text) == []
    assert redact_secrets(text) == (text, [])


def test_real_secrets_are_still_redacted():
    """收紧不能把真密钥放过去 —— 这是底线。"""
    for text in (
        "api_key = 9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "token: A39C8122FF85B3B700719B9836EA6C4D",
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "-----BEGIN RSA PRIVATE KEY-----",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ):
        _out, hits = redact_secrets(text)
        assert hits, f"漏掉了真密钥：{text}"


def test_bare_hash_still_flagged():
    """孤零零的一长串十六进制照旧可疑（没有扩展名、也不在路径里）。"""
    _out, hits = redact_secrets("值 = A39C8122FF85B3B700719B9836EA6C4D")
    assert hits
    assert hits[0].label == "长十六进制串（疑似密钥）"
