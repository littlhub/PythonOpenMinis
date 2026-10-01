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


# ---------------------------------------------------------------------------
# [T-guard-session-id-not-secret] 会话 id / 工作区目录名不是密钥
#
# 同一个洞的另一半：工作区目录名是 ``db-<会话 id>``，而会话 id 是本地
# ``uuid4().hex``（32 位十六进制）。旧判定只放过「紧挨着路径分隔符」与
# 「紧跟着扩展名」两种形态，而这里的十六进制串前面是 ``-``（``db-``）、
# 后面常常是 ``/``、``:`` 或行尾 —— 两个条件都不满足，于是：
#
#   * ``ls workspace/`` 之类工具输出里，每个会话目录都被打成
#     ``ac48…efe0「已拦截」``（前端会话列表同理）；
#   * ``zcode_bash`` 只要命令里出现工作区路径（``ls -la .../db-ac48…/``）
#     就被**整条拦下**，ZCode 干不了活、反复重试 —— 用户看到的是
#     「敏感信息一直拦」「ZCode 卡住」。
#
# 收敛后的口径：``db-<十六进制>`` 视为目录名；另外补上「紧跟着路径分隔符」
# 这一条（路径中间那一段，如 ``/workspace/9f8e…/sub``）。
# ---------------------------------------------------------------------------

#: 真实出现过的会话 id（32 位十六进制，工作区目录名由它派生）。
SESSION_ID = "ac48aaa848e344a2a51bf5856aaeefe0"
OTHER_IDS = (
    "12e7de1671e14381a670ec58a6f8de36",
    "3040b9cc3baa4b01aa821f1d6d52c038",
    "b7f3b592e4b44384804097d2dc7ea252",
)


def test_workspace_bash_command_is_not_blocked():
    """ZCode 探工作区的那条 bash 命令（含 ``db-<会话 id>`` 路径）要放行。"""
    text = (
        f"ls -la C:/Users/loo/openminis/workspace/db-{SESSION_ID}/ 2>/dev/null; "
        "echo '---'; pwd"
    )
    out, hits = redact_secrets(text)
    assert hits == []
    assert out == text


def test_session_id_dir_names_are_not_redacted():
    """``ls workspace/`` 输出里的会话目录名（可能一次好几个）全部保留。"""
    text = " ".join(f"db-{sid}" for sid in OTHER_IDS)
    assert scan_secret_text(text) == []
    assert redact_secrets(text) == (text, [])


def test_path_middle_hex_is_not_redacted():
    """路径中间那一段十六进制（后面紧跟分隔符）也算路径。"""
    text = "/var/minis/workspace/9f8e7d6c5b4a39281706f5e4d3c2b1a0/sub/a.txt"
    assert scan_secret_text(text) == []


def test_bare_hex_at_line_edges_still_flagged():
    """``db-`` 与路径之外的孤立十六进制照旧判为疑似密钥 —— 边界不能松。

    这里守着两个空串边界：命中落在**行尾**时 ``tail`` 为空、落在**行首**时
    ``left`` 为空，而 ``'' in "/\\"`` 恒为真 —— 早先的实现会把这两种位置的
    真密钥静默放行。
    """
    for text in (
        "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "the leaked key is 9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "值 = 9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    ):
        _out, hits = redact_secrets(text)
        assert hits, f"漏掉了孤立十六进制串：{text}"
