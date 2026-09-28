"""Resolved ${ENV} values are scrubbed in every form a page is likely to echo them back in.

Found by a real-browser smoke run: a login page put the typed address into a query string,
and it came back percent-encoded — past a scrub that only knew the literal value.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from html import escape
from pathlib import Path
from urllib.parse import quote, quote_plus

from core.evidence.e2e.redact import MIN_SCRUB_CHARS, scrub_resolved, scrub_text_files
from tests.checks.support import assert_true


def _test_e2e_redaction_variants() -> None:
    email = "smoke.operator@internal.example"
    password = 'p@ss word&"<x>'
    env = {"E2E_USER": email, "E2E_PASS": password, "E2E_PIN": "123"}
    lower_hex = re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), quote(password, safe=""))
    cases = {
        "raw email": (email, "${E2E_USER}"),
        "percent-encoded email": (quote(email, safe=""), "${E2E_USER}"),
        "raw password": (password, "${E2E_PASS}"),
        "path-quoted password": (quote(password), "${E2E_PASS}"),
        "form-encoded password": (quote_plus(password), "${E2E_PASS}"),
        "lower-hex password": (lower_hex, "${E2E_PASS}"),
        "html-escaped password": (escape(password, quote=True), "${E2E_PASS}"),
        "json-escaped password": (json.dumps(password)[1:-1], "${E2E_PASS}"),
    }
    for label, (echo, placeholder) in cases.items():
        clean = scrub_resolved(f"before {echo} after", env)
        assert_true(clean == f"before {placeholder} after", f"{label} is scrubbed: {clean!r}")

    assert_true(
        scrub_resolved({"url": f"/me?u={quote(email, safe='')}", "list": [email]}, env)
        == {"url": "/me?u=${E2E_USER}", "list": ["${E2E_USER}"]},
        "nested payloads are walked",
    )
    assert_true(
        scrub_resolved("pin 123 order 1234", env) == "pin 123 order 1234",
        f"values under {MIN_SCRUB_CHARS} characters are not substring-scrubbed",
    )
    assert_true(
        scrub_resolved("x alpha-secret-long y", {"E2E_A": "alpha-secret", "E2E_B": "alpha-secret-long"}) == "x ${E2E_B} y",
        "the longest value is restored whole",
    )

    root = Path(tempfile.mkdtemp(prefix="e2e-redact-"))
    try:
        (root / "step02.html").write_text(
            f"<p>Signed in as {escape(email)}</p><a href='/me?u={quote(email, safe='')}'>me</a>", encoding="utf-8"
        )
        (root / "step02.png").write_bytes(b"\x89PNG" + email.encode("utf-8"))
        scrub_text_files(root, env)
        html = (root / "step02.html").read_text(encoding="utf-8")
        assert_true(
            email not in html and quote(email, safe="") not in html and html.count("${E2E_USER}") == 2,
            f"HTML artifacts are scrubbed in place: {html}",
        )
        assert_true(
            email.encode("utf-8") in (root / "step02.png").read_bytes(),
            "binary files are left alone — which is why the player takes no screenshot once values resolved",
        )

        # Not only root-level HTML: any text suffix, in any subdirectory, any case.
        nested = root / "console" / "deep"
        nested.mkdir(parents=True)
        written = {
            nested / "console.LOG": f"typed {email} into the form",
            root / "console" / "requests.jsonl": json.dumps({"url": f"/me?u={quote_plus(email)}"}) + "\n",
            root / "notes.txt": f"user={email}",
            root / "state.json": json.dumps({"login": email}),
        }
        for path, text in written.items():
            path.write_text(text, encoding="utf-8")
        scrub_text_files(root, env)
        for path in written:
            text = path.read_text(encoding="utf-8")
            assert_true(
                email not in text and quote_plus(email) not in text and "${E2E_USER}" in text,
                f"{path.relative_to(root)} is scrubbed in place: {text}",
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Agent-written input: a typed-out value is found by the lookup, never by its shape.
    from core.evidence.e2e.redact import scrub_literals

    scenario = {
        "claims": [{"id": "login-valid-user"}],
        "steps": [
            {"action": "fill", "value": "user"},
            {"action": "fill", "value": email},
            {"action": "goto", "url": f"/profile/{email}"},
        ],
    }
    clean, names = scrub_literals(scenario, {"E2E_USER": "user", "E2E_PASS": email})
    assert_true(names == ["E2E_PASS", "E2E_USER"], f"both values are named, never shown: {names}")
    assert_true(clean["claims"][0]["id"] == "login-valid-user", "a short value is not replaced inside another word")
    assert_true(clean["steps"][0]["value"] == "${E2E_USER}", "a string that IS a short value is replaced whole")
    assert_true(clean["steps"][1]["value"] == "${E2E_PASS}" and clean["steps"][2]["url"] == "/profile/${E2E_PASS}", "a long value is replaced wherever it sits")
    assert_true(scrub_literals(scenario, {})[1] == [], "no lookup, nothing found")
    event = {"type": "progress", "step_id": "fill-password", "claim_id": "password-login", "detail": "typed password into the form"}
    scrubbed = scrub_resolved(event, {"E2E_PASS": "password"})
    assert_true(
        scrubbed["step_id"] == "fill-password" and scrubbed["claim_id"] == "password-login" and "${E2E_PASS}" in scrubbed["detail"],
        f"echoed text is scrubbed, scenario ids in events are not: {scrubbed}",
    )
    worded = {"steps": [{"id": "fill-password", "action": "fill", "value": "password", "selector": {"css": "#password"}}, {"action": "goto", "url": "/reset?to=password"}], "note": "type the passwords here"}
    clean, names = scrub_literals(worded, {"E2E_PASS": "password"})
    assert_true(
        clean["steps"][0]["id"] == "fill-password" and clean["steps"][0]["value"] == "${E2E_PASS}" and clean["steps"][0]["selector"]["css"] == "#password"
        and clean["steps"][1]["url"] == "/reset?to=${E2E_PASS}" and clean["note"] == "type the passwords here" and names == ["E2E_PASS"],
        f"a credential that is an ordinary word never rewrites an id or a longer word, only the value itself: {clean}",
    )
    embedded = {"steps": [
        {"action": "expect_dom", "selector": {"text": "Welcome user@example.test"}},
        {"action": "click", "selector": {"css": "[data-user='user@example.test']"}},
        {"action": "click", "selector": {"name": "Secret123 account"}},
    ]}
    clean, names = scrub_literals(embedded, {"E2E_USER": "user@example.test", "E2E_PASS": "Secret123"})
    assert_true(
        clean["steps"][0]["selector"]["text"] == "Welcome ${E2E_USER}"
        and clean["steps"][1]["selector"]["css"] == "[data-user='${E2E_USER}']"
        and clean["steps"][2]["selector"]["name"] == "${E2E_PASS} account"
        and names == ["E2E_PASS", "E2E_USER"],
        f"a credential that is not a plain word is found inside a longer selector: {clean}",
    )
    short_mixed = {"steps": [{"action": "expect_dom", "selector": {"text": "Welcome 1234"}}, {"action": "click", "selector": {"css": "[data-user='a1b2']"}}, {"id": "a1b2-step", "action": "click"}]}
    clean, names = scrub_literals(short_mixed, {"E2E_USER": "a1b2", "E2E_PASS": "1234"})
    assert_true(
        clean["steps"][0]["selector"]["text"] == "Welcome ${E2E_PASS}" and clean["steps"][1]["selector"]["css"] == "[data-user='${E2E_USER}']"
        and clean["steps"][2]["id"] == "a1b2-step" and names == ["E2E_PASS", "E2E_USER"],
        f"a 4-7 character value that is not a plain word is found as a token too; ids stay: {clean}",
    )
    tiny = {"steps": [{"action": "fill", "value": "pw"}, {"action": "click", "side_effect": "pw"}], "pw": "pw"}
    clean, names = scrub_literals(tiny, {"E2E_PASS": "pw"})
    assert_true(
        names == ["E2E_PASS"] and clean["steps"][0]["value"] == "${E2E_PASS}" and clean["steps"][1]["side_effect"] == "pw" and "pw" in clean,
        f"a value under four characters is replaced only as a whole `value` field; keys and other fields are left alone: {clean}",
    )
