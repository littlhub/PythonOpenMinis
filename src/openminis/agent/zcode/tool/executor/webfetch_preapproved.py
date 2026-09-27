"""WebFetch preapproved URLs — small allowlist of documentation hosts.

Ported from: packages/core/src/tool/webfetch-preapproved.ts
Original package: @zcode/core (tool)

# PORT: 源文件位于 ``tool/webfetch-preapproved.ts``（非 ``tool/executor/``）；按任务要求落到
# ``tool/executor/webfetch_preapproved.py``。导出 ``is_web_fetch_preapproved_url``。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

__all__ = ["is_web_fetch_preapproved_url"]

PREAPPROVED_HOSTS = frozenset(
    [
        "modelcontextprotocol.io",
        "agentskills.io",
        "docs.python.org",
        "en.cppreference.com",
        "docs.oracle.com",
        "learn.microsoft.com",
        "developer.mozilla.org",
        "go.dev",
        "pkg.go.dev",
        "www.php.net",
        "docs.swift.org",
        "kotlinlang.org",
        "ruby-doc.org",
        "doc.rust-lang.org",
        "www.typescriptlang.org",
        "react.dev",
        "angular.io",
        "vuejs.org",
        "nextjs.org",
        "expressjs.com",
        "nodejs.org",
        "bun.sh",
        "jquery.com",
        "getbootstrap.com",
        "tailwindcss.com",
        "d3js.org",
        "threejs.org",
        "redux.js.org",
        "webpack.js.org",
        "jestjs.io",
        "reactrouter.com",
        "docs.djangoproject.com",
        "flask.palletsprojects.com",
        "fastapi.tiangolo.com",
        "pandas.pydata.org",
        "numpy.org",
        "www.tensorflow.org",
        "pytorch.org",
        "scikit-learn.org",
        "matplotlib.org",
        "requests.readthedocs.io",
        "jupyter.org",
        "laravel.com",
        "symfony.com",
        "docs.spring.io",
        "hibernate.org",
        "tomcat.apache.org",
        "gradle.org",
        "maven.apache.org",
        "asp.net",
        "dotnet.microsoft.com",
        "blazor.net",
        "reactnative.dev",
        "docs.flutter.dev",
        "developer.apple.com",
        "developer.android.com",
        "keras.io",
        "spark.apache.org",
        "www.mongodb.com",
        "redis.io",
        "www.postgresql.org",
        "dev.mysql.com",
        "www.sqlite.org",
        "graphql.org",
        "prisma.io",
        "docs.getdbt.com",
        "docs.aws.amazon.com",
        "cloud.google.com",
        "kubernetes.io",
        "www.docker.com",
        "www.terraform.io",
        "www.ansible.com",
        "docs.stripe.com",
        "docs.netlify.com",
        "devcenter.heroku.com",
        "cypress.io",
        "selenium.dev",
        "docs.unity.com",
        "docs.unrealengine.com",
        "git-scm.com",
        "nginx.org",
        "httpd.apache.org",
    ]
)

PREAPPROVED_PATH_PREFIXES = {
    "wordpress.org": ["/documentation"],
    "huggingface.co": ["/docs"],
    "www.kaggle.com": ["/docs"],
    "vercel.com": ["/docs"],
}

# ``/%(25)*(2f|5c|2e)/iu`` —— 拒绝任何经过百分号编码的斜杠/反斜杠/点的路径遍历尝试。
_PATH_TRAVERSAL_RE = re.compile(r"%(25)*(2f|5c|2e)", re.IGNORECASE)


def is_web_fetch_preapproved_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:  # noqa: BLE001
        return False

    if parsed.hostname in PREAPPROVED_HOSTS:
        return True

    prefixes = PREAPPROVED_PATH_PREFIXES.get(parsed.hostname)
    if prefixes is None:
        return False
    if _PATH_TRAVERSAL_RE.search(parsed.path):
        return False
    return any(parsed.path == prefix or parsed.path.startswith(f"{prefix}/") for prefix in prefixes)
