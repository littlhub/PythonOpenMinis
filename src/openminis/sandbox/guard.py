"""沙箱守卫：拦截「异常删除」与「敏感信息」，可展示、可手动放行。

两类拦截（family）：

* ``delete`` —— 破坏性删除命令（``rm -rf`` / ``del /f /s`` / ``rd /s`` /
  ``Remove-Item -Recurse`` / ``shutil.rmtree`` / ``find -delete`` …）、批量删除
  （通配符或整目录）、以及**工作区之外**的删除；
* ``secret`` —— 敏感信息（密码 / 密钥 / 令牌 / 私钥，中英文都认）。既拦"读取或
  外传"这类**命令**（``cat .env`` / ``echo $OPENAI_API_KEY``），也拦**出站文本**
  里的明文凭据（发给 LLM、前端、机器人通道的内容）。

每次拦截记一条事件：异常输出（原始命令/报错）、调用目录、拦截状态。前端沙箱页
据此展示，并给出「放行」——放行范围可选：

* ``once``    只放这一次（下一条同类操作不再匹配，回到拦截）；
* ``session`` 本会话内该目录一直放行；
* ``always``  加进持久白名单，以后都不拦（落 ``guard_allowlist.json``）。

事件落 ``<data_dir>/guard_events.json``（上限 ``MAX_EVENTS`` 条，新的在前）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "GuardEvent",
    "DeleteRisk",
    "SecretHit",
    "scan_delete",
    "scan_escape",
    "scan_secret_text",
    "scan_secret_command",
    "redact_secrets",
    "check_shell_command",
    "sanitize_outbound",
    "sanitize_message_parts",
    "guard",
    "Guard",
]

#: 事件上限（超出丢最旧的）。
MAX_EVENTS = 300

#: 放行范围。
SCOPES = ("once", "session", "always")

MASK = "「已拦截·敏感信息」"


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------
@dataclass
class GuardEvent:
    id: str
    time: float
    family: str  # delete | secret
    tool: str
    session_id: str
    cwd: str
    targets: list[str]
    reasons: list[str]
    command: str
    output: str
    status: str = "blocked"  # blocked | allowed
    scope: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeleteRisk:
    reasons: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)

    @property
    def risky(self) -> bool:
        return bool(self.reasons)


@dataclass
class SecretHit:
    label: str
    kind: str  # command | text
    sample: str  # 命中片段（只留前后少量字符，避免把密钥本身抄进来）


# ---------------------------------------------------------------------------
# 破坏性删除识别
# ---------------------------------------------------------------------------
#: (正则, 说明)。大小写不敏感；写法覆盖 Unix / Windows cmd / PowerShell / Python。
_DESTRUCTIVE: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*\s+)*(-[a-z]*[rf][a-z]*)\b", "破坏性命令：rm -rf / rm -f"),
    (r"\bdel\s+(/[a-z]\s+)+", "破坏性命令：del /f /q"),
    (r"\b(rd|rmdir)\s+/s\b", "破坏性命令：rd /s 递归删目录"),
    (r"remove-item\b[^\n|;]*-recurse", "破坏性命令：Remove-Item -Recurse"),
    (r"\bshutil\.rmtree\s*\(", "破坏性命令：shutil.rmtree"),
    (r"\bos\.removedirs\s*\(", "破坏性命令：os.removedirs"),
    (r"\bfind\b[^\n|;]*\s-delete\b", "批量删除：find -delete"),
    (r"\bxargs\b[^\n|;]*\brm\b", "批量删除：xargs rm"),
    (r"\bgit\s+clean\s+-[a-z]*[fd]", "破坏性命令：git clean -fd"),
    (r"\btruncate\s+", "破坏性命令：truncate 清空"),
    (r":\s*>\s*\S", "破坏性命令：重定向清空文件"),
    (r"\bdd\s+if=.*\bof=/dev/", "破坏性命令：dd 覆写设备"),
)

#: 出现这些就当作"批量"（删整个目录 / 通配符）。
_BULK_HINTS: tuple[tuple[str, str], ...] = (
    (r"\s[-/]?r[f]?\s+['\"]?[^\s'\"]*[*?]", "批量删除：通配符目标"),
    (r"\s[-/]?r[f]?\s+['\"]?(\.{1,2}/?|/[A-Za-z]*|\*)\b", "批量删除：目录级目标"),
    (r"\bdel\b[^\n|;]*[*?]", "批量删除：del 通配符"),
)

#: 以空白/分隔符为界的**完整** token。
#:
#: 曾经用 ``findall`` 直接捞 ``/...`` 片段，结果把相对路径
#: ``scripts/image_generation.py`` 截成 ``/image_generation.py`` 当成绝对路径，
#: 于是 `cd <技能目录> && python scripts/xxx.py` 被误报「目录越界」（用户实测）。
#: 现在只按整词判断，绝不在词中间截断。
_TOKEN_RE = re.compile(r"[^\s|;&<>\"']+")

#: 伪路径：不是真文件，永远不该算「工作区之外」。
_PSEUDO_PATHS = frozenset(
    {"/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/tty", "nul"}
)


def _path_candidates(command: str) -> list[str]:
    """挑出命令里的**完整**路径 token（绝对路径，或含 ``..`` 的相对路径）。"""
    out: list[str] = []
    for m in _TOKEN_RE.finditer(command):
        tok = m.group(0).strip("\"'").rstrip(',;)>')
        if not tok or tok in out:
            continue
        if re.match(r"^[A-Za-z]:[\\/]", tok) or tok.startswith(("/", "\\")):
            out.append(tok)
        elif ".." in tok:
            out.append(tok)
    return out


#: Windows 风格的开关（``del /f /s``、``taskkill /PID``）不是路径：单个短段、
#: 无扩展名、无更多分隔符的 ``/xxx`` 一律当 flag。
_FLAG_TOKEN_RE = re.compile(r"^/[A-Za-z0-9]{1,4}$")

#: 会切换会话目录的命令。
_CD_RE = re.compile(
    r"(?:^|[|;&]\s*)(?:cd|chdir|set-location|pushd)\s+([^\n|;&]+)", re.IGNORECASE
)

#: 重定向写出（``> 路径`` / ``>> 路径``）。
_REDIRECT_RE = re.compile(r">>?\s*([^\s|;&<>]+)")

#: 读敏感文件的命令特征（中英文 key/password 都算）。
#: 注意误伤：`python -m venv env` / `cat .env.example` 这种正常命令不该被拦，
#: 所以 env 系列要求出现在**命令开头或管道/分隔符之后**，`.env` 后面不能紧跟字。
#: 三元组 = (正则, 说明, 需要校验「值」是否像真凭据的捕获组；0 = 整段命中即算)。
_SECRET_READ: tuple[tuple[str, str, int], ...] = (
    (
        r"(?:^|[|;&]\s*)\b(cat|type|more|head|tail|less)\b[^\n|;]*"
        r"(\.env(?![.\w])|\.pem\b|\.key\b|id_rsa|credentials\b|\.netrc)",
        "读取密钥文件",
        0,
    ),
    (
        r"(?:^|[|;&]\s*)\b(echo|printf)\b[^\n|;]*\$\{?[A-Z_]*(KEY|TOKEN|SECRET"
        r"|PASSWORD|PASSWD|PWD|CREDENTIAL)",
        "打印环境变量里的密钥",
        0,
    ),
    (
        r"(?:^|[|;&]\s*)\b(printenv|env)\s*(\||$|>\s*&?\d?)",
        "枚举环境变量（含密钥）",
        0,
    ),
    (r"(?:^|[|;&]\s*)get-childitem\s+env:", "枚举环境变量（PowerShell）", 0),
    (r"(?:^|[|;&]\s*)set\s*\|", "枚举环境变量", 0),
    # 光提到「密码:」不算（文档、echo、注释里常见）—— 必须有**像真凭据的值**。
    (
        r"(密码|密钥|令牌|私钥|凭据)\s*[:=：]\s*([^\s，,;；\"']{4,})",
        "中文凭据赋值",
        2,
    ),
)


def _has_glob(token: str) -> bool:
    return "*" in token or "?" in token


def _looks_like_path(token: str) -> bool:
    """过滤掉 Windows 开关（``/f`` ``/PID``）、URL 与伪路径，剩下的才算路径。"""
    raw = token.strip().strip("\"'")
    if not raw or "://" in raw:
        return False
    if _FLAG_TOKEN_RE.match(raw):
        return False
    if raw.lower() in _PSEUDO_PATHS:  # `> /dev/null` 不是写到外面
        return False
    return True


def _resolve_outside(token: str, cwd: str = "") -> bool:
    """``token``（含相对路径，按会话 ``cwd`` 解析）是否落在工作区之外。"""
    raw = token.strip().strip("\"'")
    if not _looks_like_path(raw):
        return False
    try:
        from ..tools.path_utils import readonly_roots, workspace_root

        root = workspace_root().resolve()
        base = Path(cwd).resolve() if cwd else root
        if not Path(raw).is_absolute() and not raw.startswith("~"):
            candidate = (base / raw).resolve()
        else:
            candidate = Path(raw).expanduser().resolve()
        if candidate == root or root in candidate.parents:
            return False
        for extra in readonly_roots():
            er = extra.resolve()
            if candidate == er or er in candidate.parents:
                return False
        return True
    except Exception:  # pragma: no cover - 路径层坏掉不该放行
        return False


def _outside_workspace(token: str) -> bool:
    """兼容旧调用：只判绝对路径 token。"""
    raw = token.strip().strip("\"'")
    if not raw or not Path(raw).is_absolute():
        return False
    return _resolve_outside(raw)


def scan_delete(command: str, cwd: str = "") -> DeleteRisk:
    """识别一条 shell 命令里的异常删除（相对路径按会话 ``cwd`` 解析）。"""
    if not command.strip():
        return DeleteRisk()
    low = command.lower()
    risk = DeleteRisk()
    for pattern, why in _DESTRUCTIVE:
        if re.search(pattern, low):
            risk.reasons.append(why)
    for pattern, why in _BULK_HINTS:
        if re.search(pattern, low):
            risk.reasons.append(why)
    for token in _path_candidates(command):
        if _resolve_outside(token, cwd):
            risk.targets.append(token)
    # 相对路径的越界（``rm -rf ../../x``）也归到目标里。
    if ".." in command:
        for token in re.findall(r"[\w./\\-]*\.\.[\w./\\-]*", command):
            if _resolve_outside(token, cwd) and token not in risk.targets:
                risk.targets.append(token)
    # 越界路径只有在**确实像删除**时才算删除风险；普通的"读外面文件 / cd 出去"
    # 交给 escape 家族（scan_escape）拦，否则每条带外部路径的命令都会被误记成删除。
    if risk.targets and risk.reasons:
        risk.reasons.append("工作区之外的路径：" + "、".join(risk.targets[:3]))
    if _has_glob(command) and any("删除" in r for r in risk.reasons):
        risk.reasons.append("含通配符，可能一次删掉大量文件")
    # 去重保序
    seen: list[str] = []
    for r in risk.reasons:
        if r not in seen:
            seen.append(r)
    risk.reasons = seen
    return risk


# ---------------------------------------------------------------------------
# 目录越界识别
# ---------------------------------------------------------------------------
def scan_escape(command: str, cwd: str = "") -> DeleteRisk:
    """识别一条 shell 命令里的**目录越界**。

    拦三类：

    1. ``cd`` / ``chdir`` / ``Set-Location`` / ``pushd`` 到工作区之外
       （含 ``cd ..`` 跳出工作区根、``cd C:\\Windows``、``cd ~``）；
    2. 命令里出现工作区之外的路径（``cat C:\\Users\\…``、``python G:\\…\\x.py``、
       相对路径 ``..\\..\\`` 解析后越界）；
    3. 重定向写出（``> 路径`` / ``>> 路径``）到工作区之外。
    """
    if not command.strip():
        return DeleteRisk()
    risk = DeleteRisk()

    # 1) cd 家族
    for m in _CD_RE.finditer(command):
        target = m.group(1).strip()
        if not target or target == "-":
            continue
        if _resolve_outside(target, cwd):
            risk.targets.append(target)
            risk.reasons.append(f"目录越界：cd 目标 {target} 在工作区之外")

    # 2) 命令里的越界路径（cd 目标已查过，跳过 cd 行）
    body = _CD_RE.sub(" ", command)
    for token in _path_candidates(body):
        if token in risk.targets:
            continue
        if _resolve_outside(token, cwd):
            risk.targets.append(token)
    if ".." in body:
        for token in re.findall(r"[\w./\\-]*\.\.[\w./\\-]*", body):
            if token in risk.targets:
                continue
            if _resolve_outside(token, cwd):
                risk.targets.append(token)
    if risk.targets and not risk.reasons:
        risk.reasons.append("目录越界：访问工作区之外的路径 " + "、".join(risk.targets[:3]))

    # 3) 重定向写出
    for m in _REDIRECT_RE.finditer(command):
        target = m.group(1).strip("\"'")
        if not _looks_like_path(target):
            continue
        if _resolve_outside(target, cwd):
            if target not in risk.targets:
                risk.targets.append(target)
            risk.reasons.append(f"目录越界：重定向写出 {target} 在工作区之外")

    # 去重保序
    seen: list[str] = []
    for r in risk.reasons:
        if r not in seen:
            seen.append(r)
    risk.reasons = seen
    return risk


def scan_secret_command(command: str) -> list[SecretHit]:
    """命令本身是否在读取/外传敏感信息，或直接带着明文密钥。"""
    hits: list[SecretHit] = []
    for pattern, why, group in _SECRET_READ:
        m = re.search(pattern, command, re.IGNORECASE)
        if not m:
            continue
        # 带值的规则（中文明文凭据赋值）要求值真的像凭据 ——
        # `echo "密码: 占位"`、`print('密钥:openai_key')` 这类只是提到变量/文档。
        if group > 0 and not _looks_like_real_secret(m.group(group) or ""):
            continue
        hits.append(SecretHit(label=why, kind="command", sample=_sample(command)))
    seen = {h.label for h in hits}
    for hit in scan_secret_text(command):
        if hit.label in seen:  # 同一条凭据别在事件里记两遍
            continue
        seen.add(hit.label)
        hits.append(SecretHit(label=hit.label, kind="command", sample=hit.sample))
    return hits


# ---------------------------------------------------------------------------
# 敏感信息扫描（出站文本 / 命令）
# ---------------------------------------------------------------------------
#: 明文凭据形态（高置信度，尽量不误伤普通文本）。
#: 三元组 = (正则, 说明, 需要遮的值所在捕获组；0 = 整段都遮)。
_SECRET_PATTERNS: tuple[tuple[str, str, int], ...] = (
    (r"\bsk-[A-Za-z0-9_\-]{16,}", "OpenAI 风格密钥 sk-…", 0),
    (r"\bsk-ant-[A-Za-z0-9_\-]{16,}", "Anthropic 密钥 sk-ant-…", 0),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key", 0),
    (r"\bAIza[0-9A-Za-z_\-]{30,}", "Google API Key", 0),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "GitHub Token", 0),
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "Slack Token", 0),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥文件内容", 0),
    (
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|credential)s?\b\s*[:=]\s*[\"']?([^\s\"',;]{6,})",
        "明文凭据赋值（密码/密钥）",
        2,
    ),
    (
        r"(密码|口令|密钥|令牌|私钥|凭据)\s*[:=：]\s*([^\s，,;；\"']{4,})",
        "中文明文凭据赋值",
        2,
    ),
    (r"\b[0-9a-fA-F]{32,}\b", "长十六进制串（疑似密钥）", 0),
    (r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.", "JWT Token", 0),
)


def _partial(value: str) -> str:
    """部分显示：太短就整个占位，够长则只留首尾各 4 位。"""
    v = value.strip()
    if len(v) <= 8:
        return "「已拦截」"
    return f"{v[:4]}…{v[-4:]}「已拦截」"


#: 一眼就是「占位符 / 示例」的值 —— 不遮。
_FAKE_VALUE_WORDS = frozenset(
    {
        "none", "null", "nil", "true", "false", "undefined", "nan", "empty",
        "changeme", "change_me", "placeholder", "example", "sample", "test",
        "demo", "redacted", "hidden", "masked", "todo", "fixme",
        "password", "passwd", "pwd", "secret", "token", "apikey", "api_key",
        "your_password", "yourpassword", "your_token", "yourtoken",
    }
)
_FAKE_VALUE_PREFIXES = (
    "your", "my_", "xxx", "placeholder", "example", "sample", "test", "demo",
    "todo", "change", "redacted", "某", "你的", "我的", "示例", "默认",
)
#: 本身就是「字段名」的词 —— `token = token` / `secret = secret` 是引用不是值。
_FIELD_WORDS = frozenset(
    {
        "token", "secret", "password", "passwd", "pwd", "credential",
        "api_key", "apikey", "access_key", "secret_key", "private_key",
    }
)


def _looks_like_real_secret(value: str) -> bool:
    """``xxx = value`` 里的 ``value`` 到底像不像**真凭据**。

    「明文凭据赋值」这条通用规则太容易误伤，所以只在值**确实像密钥**时才遮：

    * 不像：变量/字段名 —— ``api_key = openai_key``、``api_key = api_key``、
      ``token = my_token`` 这类是**读变量**，不是明文（用户反馈的误伤）；
    * 不像：占位符与示例 —— ``<your-token>``、``{{TOKEN}}``、``${TOKEN}``、
      ``None``、``changeme``、``xxxx``、中文说明文字；
    * 不像：代码表达式 —— ``os.environ["OPENAI_API_KEY"]``、``config.get('k')``；
    * 像：带数字/符号的高熵串 —— ``supersecret123``、``Xk92!dLm#``、``a1B2c3D4``。

    真密钥即使不命中这条通用规则，也还有 ``sk-`` / ``AKIA`` / ``ghp_`` / JWT /
    长十六进制等**高置信度**规则兜底。
    """
    v = value.strip().strip("\"'`")
    if len(v) < 8 or not v.isascii():
        return False
    low = v.lower()
    if low in _FAKE_VALUE_WORDS or low in _FIELD_WORDS:
        return False
    if low.startswith(_FAKE_VALUE_PREFIXES):
        return False
    # 占位符 / 模板 / 代码形态：<your-token> {{t}} ${T} os.environ[x] config.get('k')
    if re.match(r"^[<\[{]", v) or re.search(r"[\[\]()<>{}$]", v):
        return False
    # 蛇形标识符（openai_api_key / my_token / api_key）：下划线 + 全小写 —— 变量名
    if "_" in v and re.fullmatch(r"[a-z0-9_]+", v):
        return False
    # 必须带数字或符号 —— 纯字母词更像普通单词，不是凭据
    if not re.search(r"[0-9!@#$%^&*_\-+=:;,.?/|~]", v):
        return False
    return True


def _mask_match(match: "re.Match[str]", group: int) -> str:
    """保留 ``password=`` 这类标签，只把**值**换成部分显示。"""
    raw = match.group(0)
    if group <= 0:
        return _partial(raw) if len(raw) > 6 else MASK
    value = match.group(group)
    if not value:
        return raw
    idx = raw.rfind(value)
    if idx < 0:
        return _partial(raw)
    return raw[:idx] + _partial(value)


def _sample(text: str, limit: int = 24) -> str:
    """取命中片段的一小截（**不抄完整密钥**）。"""
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


#: 低置信度规则 —— 只在「确实像个孤立的密钥」时才遮。
#:
#: 「长十六进制串」这条特别容易误伤：QQ / 微信 / 各类缓存的媒体文件大量用
#: 哈希命名，而**文件名会随消息正文一起出站**。用户实测：
#: ``20260918-073217-qq-dd1bf336-A39C8122FF85B3B700719B9836EA6C4D.jpg``
#: 里的 32 位十六进制被当成密钥遮成「已拦截·敏感信息」，模型于是连图片路径都
#: 看不到 —— 表现就是「图片被沙箱拦截了敏感信息，模型无法识别图片」。
_LOW_CONFIDENCE_LABELS = frozenset({"长十六进制串（疑似密钥）"})

#: 扩展名尾巴 —— 命中片段紧跟着它就说明这是**文件名**，不是密钥。
_EXT_TAIL_RE = re.compile(r"\.[A-Za-z0-9]{1,8}(?![\w-])")

#: 路径分隔符（含 markdown 引用里的 ``](``）。
_PATH_LEFT_CHARS = "/\\"


def _in_path_context(text: str, start: int, end: int) -> bool:
    """命中片段是不是只是文件名 / 路径的一部分。

    只看两处最可靠的特征，避免把真正的密钥放过去：

    1. **紧跟着扩展名**（``…A39C8122FF85B3B700719B9836EA6C4D.jpg``）；
    2. **紧挨着路径分隔符**（``C:/cache/9f8e…/a.png``、``/tmp/deadbeef…``）。

    这两条之外的十六进制串照旧判为疑似密钥 —— 收紧的口子越小越安全。
    """
    tail = text[end : end + 12]
    if _EXT_TAIL_RE.match(tail):
        return True
    left = text[max(0, start - 1) : start]
    return left in _PATH_LEFT_CHARS


def _find_secret_hits(text: str) -> list[tuple["re.Match[str]", str, int]]:
    """按模式优先级取**互不重叠**的命中。

    单次扫描是必要的：以前每条规则各自 ``re.sub`` 一遍，高置信度规则已经遮成
    ``…「已拦截」``，通用规则又会在这段文字上再命中一次，产出
    ``sk-a…已拦截」「已拦截」`` 这种垃圾。现在先到先得（``sk-`` 等排在前面），
    重叠区间直接丢弃。
    """
    if not text or len(text) < 8:
        return []
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, "re.Match[str]", str, int]] = []
    for pattern, label, group in _SECRET_PATTERNS:
        for m in re.finditer(pattern, text):
            if group > 0 and not _looks_like_real_secret(m.group(group) or ""):
                continue
            s, e = m.span()
            if label in _LOW_CONFIDENCE_LABELS and _in_path_context(text, s, e):
                continue
            if any(not (e <= ts or s >= te) for ts, te in taken):
                continue
            taken.append((s, e))
            found.append((s, e, m, label, group))
    found.sort(key=lambda item: item[0])
    return [(m, label, group) for _s, _e, m, label, group in found]


def scan_secret_text(text: str) -> list[SecretHit]:
    """扫一段出站文本里的明文凭据。

    命中样本给的是**已遮版**，所以沙箱面板里也不会出现明文。
    """
    return [
        SecretHit(label=label, kind="text", sample=_sample(_mask_match(m, group)))
        for m, label, group in _find_secret_hits(text)
    ]


def redact_secrets(text: str) -> tuple[str, list[SecretHit]]:
    """把明文凭据换成**占位符 / 部分显示**，返回（脱敏文本, 命中列表）。

    ``password=Xk92!dLm#`` → ``password=Xk92…dLm#「已拦截」`` ——
    送到 LLM / 前端 / 机器人通道的就是这个版本。

    判定为「不是真凭据」的（变量名、占位符、代码表达式）原样保留 ——
    用户反馈 ``api_key = openai_key`` 被误遮，这类是**读变量**。
    """
    hits: list[SecretHit] = []
    spans: list[tuple[int, int, str]] = []
    for m, label, group in _find_secret_hits(text):
        hits.append(
            SecretHit(label=label, kind="text", sample=_sample(_mask_match(m, group)))
        )
        s, e = m.span()
        spans.append((s, e, _mask_match(m, group)))
    if not spans:
        return text, []
    spans.sort()
    out: list[str] = []
    cursor = 0
    for s, e, rep in spans:
        out.append(text[cursor:s])
        out.append(rep)
        cursor = e
    out.append(text[cursor:])
    return "".join(out), hits


# ---------------------------------------------------------------------------
# 守卫本体：事件 + 放行
# ---------------------------------------------------------------------------
class Guard:
    """拦截事件的记录、放行匹配与持久化。"""

    def __init__(self) -> None:
        self._events: list[GuardEvent] = []
        self._loaded = False
        #: 放行登记：{(family, session_id_or_'', dir_key): scope}
        self._allow: dict[tuple[str, str, str], str] = {}
        #: 持久白名单：{family: [dir_key, ...]}
        self._persistent: dict[str, list[str]] = {}
        self._persistent_loaded = False
        #: 实时检测流水（内存态，不落盘）：每条被扫描的命令一行。
        self._live: deque[dict[str, Any]] = deque(maxlen=50)

    def note_scan(
        self, *, command: str, cwd: str, family: str, reasons: list[str]
    ) -> None:
        """记录一次扫描结果（无论放行还是拦截），供沙箱页实时展示。"""
        self._live.appendleft(
            {
                "time": time.time(),
                "command": command,
                "cwd": cwd,
                "family": family,
                "reasons": reasons,
                "blocked": bool(reasons),
            }
        )

    def live(self, limit: int = 30) -> list[dict[str, Any]]:
        return list(self._live)[: max(limit, 0)]

    # -- 路径 ---------------------------------------------------------
    def _events_path(self) -> Path:
        return app_context().data_dir / "guard_events.json"

    def _allow_path(self) -> Path:
        return app_context().data_dir / "guard_allowlist.json"

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            p = self._events_path()
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                self._events = [GuardEvent(**e) for e in data if isinstance(e, dict)]
        except Exception:  # pragma: no cover - 坏文件不该挡住启动
            logger.warning("guard events 读取失败", exc_info=True)

    def _load_persistent(self) -> None:
        if self._persistent_loaded:
            return
        self._persistent_loaded = True
        try:
            p = self._allow_path()
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._persistent = {
                        k: list(v) for k, v in data.items() if isinstance(v, list)
                    }
        except Exception:  # pragma: no cover
            logger.warning("guard 白名单读取失败", exc_info=True)

    def _save_events(self) -> None:
        try:
            p = self._events_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps([e.as_dict() for e in self._events], ensure_ascii=False,
                           indent=2),
                encoding="utf-8",
            )
        except Exception:  # pragma: no cover
            logger.warning("guard events 落盘失败", exc_info=True)

    def _save_persistent(self) -> None:
        try:
            p = self._allow_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(self._persistent, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # pragma: no cover
            logger.warning("guard 白名单落盘失败", exc_info=True)

    # -- 查询 ---------------------------------------------------------
    def events(self, limit: int = 100, family: str = "") -> list[GuardEvent]:
        self.load()
        items = [e for e in self._events if not family or e.family == family]
        return items[:limit]

    def get(self, event_id: str) -> Optional[GuardEvent]:
        self.load()
        return next((e for e in self._events if e.id == event_id), None)

    def clear(self) -> None:
        self.load()
        self._events = []
        self._save_events()

    # -- 记录 ---------------------------------------------------------
    def record(
        self,
        *,
        family: str,
        tool: str,
        session_id: str,
        cwd: str,
        targets: Iterable[str],
        reasons: Iterable[str],
        command: str = "",
        output: str = "",
    ) -> GuardEvent:
        self.load()
        event = GuardEvent(
            id=f"g-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
            time=time.time(),
            family=family,
            tool=tool,
            session_id=session_id,
            cwd=cwd,
            targets=[t for t in targets if t],
            reasons=[r for r in reasons if r] or ["命中沙箱守卫规则"],
            command=command,
            output=output,
        )
        self._events.insert(0, event)
        del self._events[MAX_EVENTS:]
        self._save_events()
        logger.warning(
            "sandbox guard[%s] blocked tool=%s cwd=%s reasons=%s",
            family, tool, cwd, "；".join(event.reasons),
        )
        return event

    # -- 放行 ---------------------------------------------------------
    @staticmethod
    def dir_key(targets: Iterable[str], cwd: str = "") -> str:
        """放行的匹配键：目标目录（没有绝对目标就用调用目录）。"""
        for t in targets:
            raw = str(t).strip().strip("\"'")
            if not raw:
                continue
            try:
                p = Path(raw).expanduser()
                key = str(p if p.is_dir() else p.parent)
            except Exception:  # pragma: no cover
                key = raw
            return key.replace("\\", "/").rstrip("/").lower()
        return (cwd or "").replace("\\", "/").rstrip("/").lower()

    def is_allowed(
        self, *, family: str, session_id: str, targets: Iterable[str], cwd: str = ""
    ) -> bool:
        """这条操作是否已被放行（放行后不再拦截）。"""
        self.load()
        self._load_persistent()
        key = self.dir_key(targets, cwd)
        # 1) 持久白名单
        if key and key in [k.lower() for k in self._persistent.get(family, [])]:
            return True
        # 2) 本会话该目录
        if (family, session_id, key) in self._allow:
            return True
        # 3) 一次性（键里不带 session，用后即焚）
        token = (family, "", key)
        if token in self._allow:
            self._allow.pop(token, None)
            return True
        return False

    def allow(self, event_id: str, scope: str) -> Optional[GuardEvent]:
        """放行一条拦截记录。``scope`` = once / session / always。"""
        self.load()
        self._load_persistent()
        event = self.get(event_id)
        if event is None:
            return None
        scope = scope if scope in SCOPES else "once"
        key = self.dir_key(event.targets, event.cwd)
        if scope == "session" and key:
            self._allow[(event.family, event.session_id, key)] = "session"
        elif scope == "always" and key:
            bucket = self._persistent.setdefault(event.family, [])
            if key not in [k.lower() for k in bucket]:
                bucket.append(key)
            self._save_persistent()
        else:
            if key:
                self._allow[(event.family, "", key)] = "once"
        event.status = "allowed"
        event.scope = scope
        self._save_events()
        return event

    def deny(self, event_id: str) -> Optional[GuardEvent]:
        """明确拒绝（状态回到 blocked，并清掉可能存在的放行登记）。"""
        self.load()
        event = self.get(event_id)
        if event is None:
            return None
        key = self.dir_key(event.targets, event.cwd)
        for k in list(self._allow):
            if k[0] == event.family and k[2] == key:
                self._allow.pop(k, None)
        event.status = "blocked"
        event.scope = None
        self._save_events()
        return event

    def allowlist(self) -> dict[str, list[str]]:
        self._load_persistent()
        return {k: list(v) for k, v in self._persistent.items()}

    def revoke(self, family: str = "", key: str = "") -> dict[str, list[str]]:
        """撤销白名单（不给 key 就清空该 family / 全部）。"""
        self._load_persistent()
        if family and key:
            bucket = self._persistent.get(family, [])
            self._persistent[family] = [
                k for k in bucket if k.lower() != key.lower()
            ]
        elif family:
            self._persistent.pop(family, None)
        else:
            self._persistent = {}
        self._save_persistent()
        return self.allowlist()

    def reset(self) -> None:
        """测试用：清空内存态。"""
        self._events = []
        self._allow = {}
        self._persistent = {}
        self._loaded = False
        self._persistent_loaded = False


guard = Guard()


# ---------------------------------------------------------------------------
# 给调用方用的薄封装
# ---------------------------------------------------------------------------
def _block_message(event: GuardEvent) -> str:
    """交给模型看的拦截说明 —— 要能解释清楚"为什么被拦、下一步怎么办"。"""
    reasons = "；".join(event.reasons)
    where = event.cwd or "（未知目录）"
    label = {"delete": "异常删除", "secret": "敏感信息", "escape": "目录越界"}.get(
        event.family, event.family
    )
    return (
        "$ " + (event.command or event.output) + "\n"
        f"[沙箱拦截:{label}] {reasons}\n"
        f"调用目录: {where}\n"
        f"拦截编号: {event.id}\n"
        "这条操作已被沙箱拦下，需要用户在「沙箱」页确认后放行（可只放一次 / 本会话 / "
        "永久白名单）。不要换个写法绕过拦截；先把原因如实报告用户，等用户决定。"
    )


def check_shell_command(
    command: str, *, session_id: str, cwd: str = "", tool: str = "shell_execute"
) -> Optional[str]:
    """shell 守卫入口：一条命令**一次分析**——删除 + 越界合并成一个事件、一道放行门。"""
    if not command.strip():
        return None

    # 合并分析：删除规则与越界规则一起跑，理由和目标合到一条事件里。
    delete = scan_delete(command, cwd)
    escape = scan_escape(command, cwd)
    hits = scan_secret_command(command)
    reasons: list[str] = list(delete.reasons)
    for r in escape.reasons:
        if r not in reasons:
            reasons.append(r)
    targets = list(dict.fromkeys([*delete.targets, *escape.targets]))
    family = "delete" if delete.risky else "escape"
    secret_reasons = [f"命令涉及敏感信息：{h.label}" for h in hits]

    # 实时流水：每条命令都记（放行/拦截都显示）。
    guard.note_scan(
        command=command,
        cwd=cwd,
        family=family if reasons else ("secret" if secret_reasons else ""),
        reasons=reasons + secret_reasons,
    )

    if reasons:
        # 有删除理由按"异常删除"标示，纯越界按"目录越界"标示 —— 但门只有一道。
        if not guard.is_allowed(
            family=family, session_id=session_id, targets=targets, cwd=cwd
        ):
            event = guard.record(
                family=family,
                tool=tool,
                session_id=session_id,
                cwd=cwd,
                targets=targets,
                reasons=reasons,
                command=command,
                output=f"$ {command}",
            )
            return _block_message(event)
        return None
    if hits:
        if not guard.is_allowed(
            family="secret", session_id=session_id, targets=[], cwd=cwd
        ):
            event = guard.record(
                family="secret",
                tool=tool,
                session_id=session_id,
                cwd=cwd,
                targets=[],
                reasons=[f"命令涉及敏感信息：{h.label}" for h in hits],
                command=command,
                output=f"$ {command}\n[命中] " + "；".join(h.sample for h in hits),
            )
            return _block_message(event)
    return None


def sanitize_outbound(
    text: str,
    *,
    where: str,
    session_id: str = "",
    tool: str = "",
    scrub_paths: bool = False,
) -> str:
    """出站文本（发往 LLM / 前端 / 机器人通道）的敏感信息拦截。

    命中明文凭据时记一条 ``secret`` 事件，并返回**脱敏后**的文本；已被放行的
    场景直接原样返回。

    ``scrub_paths=True`` 时顺带把本机绝对路径换成沙箱写法
    （``C:/Users/<名>/…/workspace/x.png`` → ``/var/minis/workspace/x.png``）。
    **只对发往模型的内容开**：前端拿这个路径去 ``/api/fs/raw`` 取图，换成沙箱
    写法它就加载不出来了。
    """
    if not text or len(text) < 8:
        return text
    cleaned, hits = redact_secrets(text)
    if hits:
        key_cwd = f"outbound:{where}"
        if not guard.is_allowed(
            family="secret", session_id=session_id, targets=[], cwd=key_cwd
        ):
            guard.record(
                family="secret",
                tool=tool or f"outbound:{where}",
                session_id=session_id,
                cwd=key_cwd,
                targets=[],
                reasons=[f"出站内容含明文凭据：{h.label}" for h in hits],
                command="",
                output=f"[{where}] " + "；".join(h.sample for h in hits),
            )
            text = cleaned
    if scrub_paths:
        from ..tools.path_utils import scrub_machine_paths

        text = scrub_machine_paths(text)
    return text


def sanitize_message_parts(messages: Iterable[Any], *, session_id: str = "") -> int:
    """就地脱敏消息里的文本（发给 LLM 前的最后一道）。

    ``Text.text`` / ``ToolResult.content`` / 纯文本 ``content`` 都会被扫描，
    命中明文凭据就换成部分显示；返回被改动的条数。用 ``dataclasses.replace``
    换新部件，保持原类型（ToolResult 的 id/name 等字段不能丢）。

    这里同时把本机绝对路径换成沙箱写法（``scrub_paths=True``）—— 模型不需要
    知道 ``C:/Users/<用户名>/…``，工作区内的东西用 ``/var/minis/workspace/…``
    表达就够了，回填给工具也能解析回来。
    """
    import dataclasses

    changed = 0
    for msg in messages:
        parts = (
            getattr(msg, "content_parts", None)
            or getattr(msg, "contentParts", None)
            or []
        )
        for i, part in enumerate(parts):
            for attr in ("text", "content"):
                raw = getattr(part, attr, None)
                if not isinstance(raw, str) or len(raw) < 8:
                    continue
                cleaned = sanitize_outbound(
                    raw, where="llm", session_id=session_id, scrub_paths=True
                )
                if cleaned != raw:
                    try:
                        parts[i] = dataclasses.replace(part, **{attr: cleaned})
                    except Exception:  # pragma: no cover - 非 dataclass 就跳过
                        pass
                    changed += 1
                break
        content = getattr(msg, "content", None)
        if isinstance(content, str) and len(content) >= 8:
            cleaned = sanitize_outbound(
                content, where="llm", session_id=session_id, scrub_paths=True
            )
            if cleaned != content:
                try:
                    msg.content = cleaned
                except Exception:  # pragma: no cover
                    pass
                changed += 1
    return changed
