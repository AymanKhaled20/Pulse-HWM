"""Task-Manager-style app -> background-process adoption (Brave RAM bug)."""

from __future__ import annotations

from pulse_hwm.processes import (
    CATEGORY_APPS,
    CATEGORY_SERVICES,
    ProcessRow,
    app_families,
    group_processes,
)

BRAVE_DIR = "C:\\Users\\us\\AppData\\Local\\BraveSoftware\\Application"


def row(pid=1, name="app.exe", exe="", username="", visible=None, category=None):
    r = ProcessRow(
        pid=pid, name=name, username=username, exe=exe, visible_window=visible
    )
    if category is not None:
        r.category = category
    return r


def test_app_families_adopts_background_in_same_install_dir():
    # Brave's helper processes run out of Brave's own folder: they belong
    # to the app, so "Brave" reports its FULL RAM, not just the main exe
    rows = [
        row(
            pid=1,
            name="brave.exe",
            exe=BRAVE_DIR + "\\brave.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
        row(
            pid=2,
            name="brave_helpers.exe",
            exe=BRAVE_DIR + "\\brave_helpers.exe",
            visible=False,
        ),
        row(
            pid=3,
            name="spotify.exe",
            exe="C:\\Apps\\Spotify\\spotify.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
    ]
    fam = app_families(rows)
    assert set(fam) == {1}
    assert [r.pid for r in fam[1]] == [2]


def test_app_families_name_family_fallback_when_exe_hidden():
    # access-denied rows have no exe path: same name family still matches
    rows = [
        row(
            pid=1,
            name="brave.exe",
            exe=BRAVE_DIR + "\\brave.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
        row(pid=5, name="bravesvc.exe", exe=""),
    ]
    assert [r.pid for r in app_families(rows).get(1, [])] == [5]


def test_unrelated_background_process_is_not_adopted():
    rows = [
        row(
            pid=1,
            name="brave.exe",
            exe=BRAVE_DIR + "\\brave.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
        row(pid=9, name="weirdtool.exe", exe="C:\\Weird\\weirdtool.exe"),
    ]
    assert app_families(rows) == {}


def test_windows_services_rows_never_get_adopted():
    # classify() already refiles service-account rows; app_families must
    # respect that so OS jobs can't be mis-parented onto an app
    rows = [
        row(
            pid=1,
            name="brave.exe",
            exe=BRAVE_DIR + "\\brave.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
        row(
            pid=7,
            name="some_helper.exe",
            exe=BRAVE_DIR + "\\some_helper.exe",
            username="NT AUTHORITY\\SYSTEM",
            category=CATEGORY_SERVICES,
        ),
    ]
    assert app_families(rows) == {}


def test_no_visible_apps_means_no_adoptions():
    rows = [
        row(
            pid=2,
            name="helper.exe",
            exe=BRAVE_DIR + "\\helper.exe",
            visible=False,
        )
    ]
    assert app_families(rows) == {}


def test_group_processes_output_is_untouched_by_families():
    # the collector keeps group buckets and only omits family kids itself
    rows = [
        row(
            pid=1,
            name="brave.exe",
            exe=BRAVE_DIR + "\\brave.exe",
            visible=True,
            category=CATEGORY_APPS,
        ),
        row(pid=2, name="brave_helper.exe", exe=BRAVE_DIR + "\\brave_helper.exe"),
    ]
    buckets = group_processes(rows)
    assert [r.pid for r in buckets["APPLICATIONS"]] == [1]
    assert [r.pid for r in buckets["BACKGROUND"]] == [2]
    adopted = {kid.pid for kids in app_families(rows).values() for kid in kids}
    assert adopted == {2}
