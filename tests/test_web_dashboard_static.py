"""Static-asset checks for the Phase 10B saved-view GUI.

These do not drive a real browser (see HANDOFF.md on browser-automation
instability) -- they assert the packaged HTML contains the required
controls/text and that the embedded dashboard JavaScript is syntactically
valid, parsed independently with Node.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from semi_intel.web.app import STATIC_DIR

INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def test_saved_view_panel_controls_present():
    for expected in (
        'id="saved-view-active"',
        'id="saved-notification-views"',
        'onclick="openSavedViewEditor()"',  # New view
        'onclick="clearActiveSavedView()"',  # Clear view
        "applySavedNotificationView(",
        "openSavedViewEditor(",
        "duplicateSavedNotificationView(",
        "deleteSavedNotificationView(",
    ):
        assert expected in INDEX_HTML, f"missing: {expected}"


def test_saved_view_editor_has_full_filter_controls():
    for expected in (
        'id="saved-view-dialog"',
        'id="sv-name"',
        'id="sv-state"',
        'id="sv-event-types"',
        'id="sv-severities"',
        'id="sv-topics"',
        'id="sv-date-window"',
        'id="sv-search"',
        'id="sv-sort"',
        'onclick="saveSavedViewFromEditor()"',
        'onclick="closeSavedViewEditor()"',
    ):
        assert expected in INDEX_HTML, f"missing: {expected}"


def test_date_window_options_match_supported_values():
    dialog = INDEX_HTML[INDEX_HTML.index('id="sv-date-window"'):]
    dialog = dialog[:dialog.index("</select>")]
    values = re.findall(r'<option value="([^"]*)">', dialog)
    assert values == ["", "1", "3", "7", "14", "30", "90"]


def test_sort_options_match_supported_values():
    dialog = INDEX_HTML[INDEX_HTML.index('id="sv-sort"'):]
    dialog = dialog[:dialog.index("</select>")]
    values = re.findall(r'<option value="([^"]*)">', dialog)
    assert values == ["newest", "oldest", "severity"]


def test_state_options_match_supported_values():
    dialog = INDEX_HTML[INDEX_HTML.index('id="sv-state"'):]
    dialog = dialog[:dialog.index("</select>")]
    values = re.findall(r'<option value="([^"]*)">', dialog)
    assert values == ["unread", "read", "dismissed", "all"]


def test_usability_states_present():
    for expected in (
        "Loading saved views",  # loading state
        "No saved views yet",  # empty state
        "Filters changed",  # modified-but-not-resaved indicator
        "Viewing:",  # active view label
    ):
        assert expected in INDEX_HTML, f"missing: {expected}"


def test_no_raw_json_or_new_frontend_dependency_introduced():
    assert "JSON.stringify(view)" not in INDEX_HTML  # old apply-by-blob pattern removed
    for forbidden in ("react", "vue", "svelte", "bootstrap.min", "cdn.jsdelivr", "unpkg.com"):
        assert forbidden not in INDEX_HTML.lower()


def test_delete_requires_confirmation():
    start = INDEX_HTML.index("async function deleteSavedNotificationView")
    body = INDEX_HTML[start:start + 400]
    assert "confirm(" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available on PATH")
def test_dashboard_javascript_parses_with_node(tmp_path):
    match = re.search(r"<script>(.*)</script>", INDEX_HTML, re.S)
    assert match, "no <script> block found in index.html"
    script_path = tmp_path / "dashboard.js"
    script_path.write_text(match.group(1), encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(script_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
