"""WebFetch pre-approval allowlist.

Ported from: packages/core/src/tool/webfetch-preapproved.ts
Original package: @zcode/core (tool)

# PORT: ``new URL(...)`` → ``urllib.parse.urlparse`` — the pre-approval check
# only inspects host / pathname, so no full URL validation is required. The
# regex on percent-encoded path characters keeps its ``u`` semantics (Python 3
# strings are already Unicode).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

__all__ = [
    "PREAPPROVED_HOSTS",
    "PREAPPROVED_PATH_PREFIXES",
    "is_web_fetch_preapproved_url",
]

PREAPPROVED_HOSTS: frozenset[str] = frozenset(
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

PREAPPROVED_PATH_PREFIXES: dict[str, list[str]] = {
    "wordpress.org": ["/documentation"],
    "huggingface.co": ["/docs"],
    "www.kaggle.com": ["/docs"],
    "vercel.com": ["/docs"],
}

# PORT: TS /%(25)*(2f|5c|2e)/iu — URL-decoded / \ . sequences in the path.
_PERCENT_ENCODED_RE = re.compile(r"%25*(2f|5c|2e)", re.IGNORECASE)


def is_web_fetch_preapproved_url(value: str) -> bool:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        # ``new URL(value)`` threw → upstream returns false.
        return False

    if parsed.hostname in PREAPPROVED_HOSTS:
        return True

    prefixes = PREAPPROVED_PATH_PREFIXES.get(parsed.hostname)
    if prefixes is None:
        return False
    if _PERCENT_ENCODED_RE.search(parsed.path):
        return False
    path = parsed.path
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in prefixes
    )
