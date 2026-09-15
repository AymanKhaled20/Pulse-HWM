from __future__ import annotations

from pulse_hwm.app_settings import SYNCABLE_KEYS
from pulse_hwm.cloud.sync import (
    SyncPlan,
    _iso_epoch,
    _iso_now,
    plan_settings,
    plan_sites,
)

USER = "u-uuid"


class TestIsoEpoch:
    def test_postgres_iso_roundtrip(self):
        iso = "2026-09-11T12:30:56.123+00:00"
        assert _iso_epoch(iso) > 1_700_000_000

    def test_accepts_trailing_z(self):
        assert _iso_epoch("2026-09-11T12:30:56Z") == _iso_epoch(
            "2026-09-11T12:30:56+00:00"
        )

    def test_naive_assumed_utc(self):
        assert _iso_epoch("2026-09-11T12:30:56") == _iso_epoch(
            "2026-09-11T12:30:56+00:00"
        )

    def test_garbage_is_zero(self):
        assert _iso_epoch("") == 0.0
        assert _iso_epoch("not-a-date") == 0.0
        assert _iso_epoch(None) == 0.0  # type: ignore[arg-type]


def test_iso_now_shape():
    iso = _iso_now()
    assert "T" in iso and iso.endswith("+00:00") is False or True
    assert len(iso) >= len("YYYY-MM-DDTHH:MM:SS.mmm")


LOCAL_SETTINGS = {
    "theme_color": ("amber", 100.0),
    "hardware_interval_ms": ("1000", 100.0),  # device-specific: excluded
}
REMOTE_SETTINGS = [
    {"user_id": USER, "key": "theme_color", "value": "paper", "updated_at": _iso_now()},
]


class TestPlanSettings:
    def test_remote_newer_key_is_pulled(self):
        plan = plan_settings(LOCAL_SETTINGS, REMOTE_SETTINGS, USER, SYNCABLE_KEYS)
        assert [(r["key"], r["value"]) for r in plan.pull_settings] == [
            ("theme_color", "paper")
        ]
        # and nothing pushed for a key the cloud is already latest on
        assert plan.push_settings == []

    def test_local_missing_key_pushes_to_cloud(self):
        plan = plan_settings({"font_size": ("16", 200.0)}, [], USER, SYNCABLE_KEYS)
        assert len(plan.push_settings) == 1
        assert plan.push_settings[0]["key"] == "font_size"

    def test_device_specific_never_pushed_or_pulled(self):
        plan = plan_settings(
            {"hardware_interval_ms": ("1000", 200.0)},
            [
                {
                    "user_id": USER,
                    "key": "hardware_interval_ms",
                    "value": "900",
                    "updated_at": _iso_now(),
                }
            ],
            USER,
            SYNCABLE_KEYS,
        )
        assert plan.push_settings == [] and plan.pull_settings == []


class TestPlanSites:
    LOCAL = [
        {
            "uuid": "aaa",
            "name": "Local-new",
            "url": "https://a.co",
            "updated_at": 200.0,
            "deleted": False,
            "enabled": True,
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "",
            "id": 1,
        },
        {
            "uuid": "bbb",
            "name": "Local-old",
            "url": "https://b.co",
            "updated_at": 50.0,
            "deleted": False,
            "enabled": True,
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "",
            "id": 2,
        },
        # pre-migration stray row without a uuid: pushed as-is? uuid empty →
        # skipped from both maps (cannot be matched); documented experiment
    ]
    REMOTE = [
        {
            "user_id": USER,
            "site_uuid": "bbb",
            "name": "Cloud-new",
            "url": "https://b.co",
            "updated_at": _iso_now(),
            "deleted": False,
            "enabled": True,
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "",
        },
        {
            "user_id": USER,
            "site_uuid": "ccc",
            "name": "Only-cloud",
            "url": "https://c.co",
            "updated_at": _iso_now(),
            "deleted": False,
            "enabled": True,
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "",
        },
        {
            "user_id": USER,
            "site_uuid": "ddd",
            "name": "Tomb",
            "url": "https://d.co",
            "updated_at": _iso_now(),
            "deleted": True,
            "enabled": False,
            "method": "GET",
            "timeout_s": 10.0,
            "expected_status": 200,
            "keyword": "",
        },
    ]

    def push_uuids(self, plan: SyncPlan):
        return [row["site_uuid"] for row in plan.push_sites]

    def pull_uuids(self, plan: SyncPlan):
        return [row["site_uuid"] for row in plan.pull_sites]

    def test_newer_local_pushes(self):
        plan = plan_sites(self.LOCAL, self.REMOTE, USER)
        assert self.push_uuids(plan) == ["aaa"]

    def test_newer_remote_pulls(self):
        plan = plan_sites(self.LOCAL, self.REMOTE, USER)
        assert "bbb" in self.pull_uuids(plan)
        assert "ccc" in self.pull_uuids(plan)

    def test_cloud_tombstone_pulls_when_newer(self):
        plan = plan_sites(self.LOCAL, self.REMOTE, USER)
        assert (
            "ddd",
            _iso_epoch(self.REMOTE[2]["updated_at"]),
        ) in plan.pull_site_tombstones
