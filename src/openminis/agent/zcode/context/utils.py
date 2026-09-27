"""Token estimation — shared character-based token estimator.

Ported from: packages/core/src/context/utils.ts
Original package: @zcode/core (context)
"""

from __future__ import annotations

import math
import re

__all__ = ["estimate_tokens"]

# PORT: 原 `import { ESTIMATED_TOKEN_CHAR_DIVISOR } from "@zcode/shared"`。
# 该常量定义在 packages/shared/src/usage-stats.ts（仓库根 packages/shared，
# 非 apps/zcode-cli 内），值为 3。Python 侧没有跨包 shared 层，按原值内联。
ESTIMATED_TOKEN_CHAR_DIVISOR = 3

# PORT: TS 正则 ``/[一-鿿]/g`` 直接照搬（U+4E00–U+9FFF，CJK 统一表意文字）。
_CHINESE_CHAR_PATTERN = re.compile("[一-鿿]")


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量
    使用共享字符估算除数，中文字符保留额外权重
    这是一个粗略估算，对于调试和监控足够用
    """
    # 中文字符通常比英文字符占用更多 token，因此按两个估算字符计入。
    chinese_chars = len(_CHINESE_CHAR_PATTERN.findall(text))
    other_chars = len(text) - chinese_chars

    # PORT: TS ``Math.ceil((chineseChars * 2 + otherChars) / DIVISOR)``。
    return math.ceil((chinese_chars * 2 + other_chars) / ESTIMATED_TOKEN_CHAR_DIVISOR)
