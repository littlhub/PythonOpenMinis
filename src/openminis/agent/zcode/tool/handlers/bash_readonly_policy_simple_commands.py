"""Bash readonly policy — simple single-word commands and global git flags.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-simple-commands.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re

from .bash_readonly_policy_callbacks import (
    date_command_is_dangerous,
    jq_command_is_dangerous,
    lsof_command_is_dangerous,
    man_command_is_dangerous,
    ps_command_is_dangerous,
    pyright_command_is_dangerous,
    sed_command_is_dangerous,
    ss_command_is_dangerous,
    test_command_is_dangerous,
    tput_command_is_dangerous,
    xargs_command_is_dangerous,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

# PORT: TS 从 barrel ``./bash-readonly-policy-flags.js`` 导入；Python 侧 barrel 只是
# ``from ... import *`` 转发，因此这里直接引用具体模块，等价且更稳。
from .bash_readonly_policy_flags_file import (
    BASE64_SAFE_FLAGS,
    DATE_SAFE_FLAGS,
    FD_SAFE_FLAGS,
    HOSTNAME_SAFE_FLAGS,
    SHA_SUM_SAFE_FLAGS,
    SORT_SAFE_FLAGS,
)
from .bash_readonly_policy_flags_process import (
    LSOF_SAFE_FLAGS,
    MAN_SAFE_FLAGS,
    NETSTAT_SAFE_FLAGS,
    PGREP_SAFE_FLAGS,
    PS_SAFE_FLAGS,
    PYRIGHT_SAFE_FLAGS,
    SS_SAFE_FLAGS,
    TEST_SAFE_FLAGS,
    TPUT_SAFE_FLAGS,
)
from .bash_readonly_policy_flags_text import (
    CAT_SAFE_FLAGS,
    FILE_SAFE_FLAGS,
    GREP_SAFE_FLAGS,
    HEAD_TAIL_SAFE_FLAGS,
    JQ_SAFE_FLAGS,
    RG_SAFE_FLAGS,
    SED_SAFE_FLAGS,
    TREE_SAFE_FLAGS,
    WC_SAFE_FLAGS,
    XARGS_SAFE_FLAGS,
)

# PORT: TS ``new Set([...])`` → ``frozenset``（只读语义更贴合）。
GIT_GLOBAL_NO_VALUE_FLAGS: frozenset[str] = frozenset(["--no-pager", "--paginate"])
GIT_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset()
GIT_GLOBAL_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    [
        "-c",
        "-C",
        "--attr-source",
        "--bare",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--shallow-file",
        "--super-prefix",
        "--work-tree",
    ]
)

# PORT: TS ``new Map<string, BashReadonlyCommandPolicy>([...])`` → ``dict``（Python 3.7+
# 保序，与 JS Map 的迭代顺序一致）。
READONLY_COMMAND_POLICIES: dict[str, BashReadonlyCommandPolicy] = {
    "alias": BashReadonlyCommandPolicy(command_only=True),
    "base64": BashReadonlyCommandPolicy(
        safe_flags=BASE64_SAFE_FLAGS, respects_double_dash=False
    ),
    "cat": BashReadonlyCommandPolicy(safe_flags=CAT_SAFE_FLAGS),
    "cd": BashReadonlyCommandPolicy(allow_any_args=True),
    "cut": BashReadonlyCommandPolicy(allow_any_args=True),
    "date": BashReadonlyCommandPolicy(
        safe_flags=DATE_SAFE_FLAGS,
        additional_command_is_dangerous_callback=date_command_is_dangerous,
    ),
    "diff": BashReadonlyCommandPolicy(allow_any_args=True),
    "dirname": BashReadonlyCommandPolicy(allow_any_args=True),
    "du": BashReadonlyCommandPolicy(allow_any_args=True),
    "echo": BashReadonlyCommandPolicy(allow_any_args=True),
    "egrep": BashReadonlyCommandPolicy(safe_flags=GREP_SAFE_FLAGS),
    "false": BashReadonlyCommandPolicy(command_only=True),
    "fd": BashReadonlyCommandPolicy(safe_flags=FD_SAFE_FLAGS),
    "file": BashReadonlyCommandPolicy(safe_flags=FILE_SAFE_FLAGS),
    "find": BashReadonlyCommandPolicy(allow_any_args=True),
    "fgrep": BashReadonlyCommandPolicy(safe_flags=GREP_SAFE_FLAGS),
    "fdfind": BashReadonlyCommandPolicy(safe_flags=FD_SAFE_FLAGS),
    "grep": BashReadonlyCommandPolicy(safe_flags=GREP_SAFE_FLAGS),
    "head": BashReadonlyCommandPolicy(safe_flags=HEAD_TAIL_SAFE_FLAGS),
    "help": BashReadonlyCommandPolicy(
        safe_flags={"-d": "none", "-m": "none", "-s": "none"}
    ),
    "hexdump": BashReadonlyCommandPolicy(allow_any_args=True),
    "hostname": BashReadonlyCommandPolicy(
        safe_flags=HOSTNAME_SAFE_FLAGS,
        # PORT: JS ``/^hostname(?:\s+(?:-[a-zA-Z]|--[a-zA-Z-]+))*\s*$/``。
        regex=re.compile(r"^hostname(?:\s+(?:-[a-zA-Z]|--[a-zA-Z-]+))*\s*$"),
    ),
    "id": BashReadonlyCommandPolicy(allow_any_args=True),
    "jq": BashReadonlyCommandPolicy(
        safe_flags=JQ_SAFE_FLAGS,
        additional_command_is_dangerous_callback=jq_command_is_dangerous,
    ),
    "lsof": BashReadonlyCommandPolicy(
        safe_flags=LSOF_SAFE_FLAGS,
        additional_command_is_dangerous_callback=lsof_command_is_dangerous,
    ),
    "ls": BashReadonlyCommandPolicy(allow_any_args=True),
    "man": BashReadonlyCommandPolicy(
        safe_flags=MAN_SAFE_FLAGS,
        additional_command_is_dangerous_callback=man_command_is_dangerous,
    ),
    "md5sum": BashReadonlyCommandPolicy(safe_flags=SHA_SUM_SAFE_FLAGS),
    "netstat": BashReadonlyCommandPolicy(safe_flags=NETSTAT_SAFE_FLAGS),
    "nl": BashReadonlyCommandPolicy(allow_any_args=True),
    "pgrep": BashReadonlyCommandPolicy(safe_flags=PGREP_SAFE_FLAGS),
    "ps": BashReadonlyCommandPolicy(
        safe_flags=PS_SAFE_FLAGS,
        additional_command_is_dangerous_callback=ps_command_is_dangerous,
    ),
    "pyright": BashReadonlyCommandPolicy(
        safe_flags=PYRIGHT_SAFE_FLAGS,
        respects_double_dash=False,
        additional_command_is_dangerous_callback=pyright_command_is_dangerous,
    ),
    "printf": BashReadonlyCommandPolicy(allow_any_args=True),
    "pwd": BashReadonlyCommandPolicy(command_only=True),
    "readlink": BashReadonlyCommandPolicy(allow_any_args=True),
    "realpath": BashReadonlyCommandPolicy(allow_any_args=True),
    "rev": BashReadonlyCommandPolicy(allow_any_args=True),
    "rg": BashReadonlyCommandPolicy(safe_flags=RG_SAFE_FLAGS),
    "sed": BashReadonlyCommandPolicy(
        safe_flags=SED_SAFE_FLAGS,
        additional_command_is_dangerous_callback=sed_command_is_dangerous,
    ),
    "sha1sum": BashReadonlyCommandPolicy(safe_flags=SHA_SUM_SAFE_FLAGS),
    "sha256sum": BashReadonlyCommandPolicy(safe_flags=SHA_SUM_SAFE_FLAGS),
    "sort": BashReadonlyCommandPolicy(safe_flags=SORT_SAFE_FLAGS),
    "ss": BashReadonlyCommandPolicy(
        safe_flags=SS_SAFE_FLAGS,
        additional_command_is_dangerous_callback=ss_command_is_dangerous,
    ),
    "stat": BashReadonlyCommandPolicy(allow_any_args=True),
    "strings": BashReadonlyCommandPolicy(allow_any_args=True),
    "tail": BashReadonlyCommandPolicy(safe_flags=HEAD_TAIL_SAFE_FLAGS),
    "test": BashReadonlyCommandPolicy(
        safe_flags=TEST_SAFE_FLAGS,
        respects_double_dash=False,
        additional_command_is_dangerous_callback=test_command_is_dangerous,
    ),
    "tput": BashReadonlyCommandPolicy(
        safe_flags=TPUT_SAFE_FLAGS,
        additional_command_is_dangerous_callback=tput_command_is_dangerous,
    ),
    "tr": BashReadonlyCommandPolicy(allow_any_args=True),
    "tree": BashReadonlyCommandPolicy(safe_flags=TREE_SAFE_FLAGS),
    "true": BashReadonlyCommandPolicy(command_only=True),
    "uniq": BashReadonlyCommandPolicy(allow_any_args=True),
    "uname": BashReadonlyCommandPolicy(allow_any_args=True),
    "wc": BashReadonlyCommandPolicy(safe_flags=WC_SAFE_FLAGS),
    "which": BashReadonlyCommandPolicy(allow_any_args=True),
    "whoami": BashReadonlyCommandPolicy(command_only=True),
    "xargs": BashReadonlyCommandPolicy(
        safe_flags=XARGS_SAFE_FLAGS,
        additional_command_is_dangerous_callback=xargs_command_is_dangerous,
    ),
    "[": BashReadonlyCommandPolicy(
        safe_flags=TEST_SAFE_FLAGS,
        respects_double_dash=False,
        additional_command_is_dangerous_callback=test_command_is_dangerous,
    ),
    "[[": BashReadonlyCommandPolicy(
        safe_flags=TEST_SAFE_FLAGS,
        respects_double_dash=False,
        additional_command_is_dangerous_callback=test_command_is_dangerous,
    ),
}

# PORT: TS ``new Set([...])`` → ``frozenset``。
READONLY_ALLOW_ANY_ARG_COMMANDS: frozenset[str] = frozenset(
    [
        "basename",
        "cal",
        "cat",
        "cmp",
        "column",
        "comm",
        "cut",
        "df",
        "diff",
        "dirname",
        "du",
        "expand",
        "expr",
        "false",
        "fmt",
        "fold",
        "free",
        "groups",
        "head",
        "hexdump",
        "id",
        "locale",
        "nl",
        "nproc",
        "numfmt",
        "od",
        "paste",
        "pr",
        "readlink",
        "realpath",
        "rev",
        "seq",
        "sleep",
        "stat",
        "strings",
        "tac",
        "tail",
        "tr",
        "true",
        "tsort",
        "type",
        "uname",
        "unexpand",
        "uptime",
        "wc",
        "which",
    ]
)

# PORT: TS ``[...] as const`` → 有序 ``list``（保留顺序语义，元素为二元组）。
READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES: list[tuple[str, str]] = [
    ("docker", "images"),
    ("docker", "ps"),
]

__all__ = [
    "GIT_GLOBAL_NO_VALUE_FLAGS",
    "GIT_GLOBAL_VALUE_FLAGS",
    "GIT_GLOBAL_DANGEROUS_FLAGS",
    "READONLY_COMMAND_POLICIES",
    "READONLY_ALLOW_ANY_ARG_COMMANDS",
    "READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES",
]
