#!/usr/bin/env python
"""OPUS-13: minify a JS source from stdin to stdout via rjsmin.

Lives next to ``build_frontend_bundle.sh`` so the bash script can
shell out without inlining a heredoc. Tests import
``minify_js_text`` directly so the same minifier covers both the
real build and the ``test_app_bundle_matches_build_script_sources``
contract check.
"""
from __future__ import annotations

import sys

import rjsmin


def minify_js_text(source: str) -> str:
    """Strip whitespace + comments, preserving semantics.

    rjsmin keeps identifiers intact, so the output is functionally
    identical to the source — no risk of a renamed exported
    callable breaking ``window.App.analyticsApp`` etc.
    """
    return rjsmin.jsmin(source, keep_bang_comments=False)


def main() -> int:
    sys.stdout.write(minify_js_text(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
