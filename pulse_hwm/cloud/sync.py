from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# —— pure planning (no I/O — every decision is unit-testable) —————————
#
# LWW = last-write-wins by updated_at.
#   settings : compared per KEY   (device-specific keys never sync at all)
#   sites    : compared per site_uuid (tombstones delete across devices)
#
# Cloud timestamps are ISO-8601 (Postgres), local ones are time.time()
# epoch floats. Mixing the formats is THE classic LWW bug, so the
# conversion helper is strict and tested.


def _iso_epoch(value: str) -> float:
    """Postgres '2026-09-11T12:34:56.7+00:00' → Unix seconds. 0 on error."""
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _iso_now() -> str:
    """Local now as the ISO-8601 shape Postgres expects."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class SyncPlan:
    """Everything the executor must push (REST) and pull (local DB)."""

    push_settings: list[dict] = field(default_factory=list)  # PostgREST rows
    pull_settings: list[dict] = field(default_factory=list)  # {key, value, ts}
    push_sites: list[dict] = field(default_factory=list)  # PostgREST rows
    # full cloud rows to adopt locally (insert AND update both land here)
    pull_sites: list[dict] = field(default_factory=list)
    # uuids whose cloud row is tombstoned: (site_uuid, cloud_epoch_ts)
    # tombstone locally too (when the cloud write is newer)
    pull_site_tombstones: list[tuple[str, float]] = field(default_factory=list)

    def is_empty_plan(self) -> bool:
        return not (
            self.push_settings
            or self.pull_settings
            or self.push_sites
            or self.pull_sites
            or self.pull_site_tombstones
        )


def plan_settings(
    local: dict[str, tuple[str, float]],
    remote: list[dict],
    user_id: str,
    syncable_keys: set[str],
) -> SyncPlan:
    """Compare per-key. Remote rows: {user_id, key, value, updated_at}."""
    plan = SyncPlan()
    remote_by_key = {str(r.get("key")): r for r in remote if r.get("key")}
    for key, (value, ts) in local.items():
        if key not in syncable_keys:
            continue  # device-specific: never leaves this machine
        cloud = remote_by_key.get(key)
        cloud_ts = _iso_epoch(cloud.get("updated_at", "")) if cloud else 0.0
        if cloud is None or float(cloud_ts or 0) < float(ts):
            plan.push_settings.append(
                {
                    "user_id": user_id,
                    "key": key,
                    "value": value,
                    "updated_at": _iso_now(),
                }
            )
    for key, cloud in remote_by_key.items():
        if key not in syncable_keys:
            continue
        cloud_ts = _iso_epoch(cloud.get("updated_at", ""))
        local_entry = local.get(key)
        local_ts = float(local_entry[1]) if local_entry else 0.0
        if cloud_ts > local_ts:
            plan.pull_settings.append(
                {"key": key, "value": str(cloud.get("value", "")), "ts": cloud_ts}
            )
    return plan


def plan_sites(local: list[dict], remote: list[dict], user_id: str) -> SyncPlan:
    """local rows: dicts with uuid/name/â€¦/updated_at/deleted."""
    plan = SyncPlan()
    remote_by_uuid = {str(r.get("site_uuid")): r for r in remote if r.get("site_uuid")}
    local_by_uuid = {str(r.get("uuid") or ""): r for r in local if r.get("uuid")}
    # local → cloud (push when local is newer or the cloud row is missing)
    for uuid, row in local_by_uuid.items():
        cloud = remote_by_uuid.get(uuid)
        if cloud is None or _iso_epoch(cloud.get("updated_at", "")) < float(
            row.get("updated_at") or 0
        ):
            plan.push_sites.append(local_row_to_rest(row, user_id))
    # cloud → local (pull adds/updates/tombstones)
    for uuid, cloud in remote_by_uuid.items():
        local_row = local_by_uuid.get(uuid)
        cloud_ts = _iso_epoch(cloud.get("updated_at", ""))
        local_ts = float((local_row or {}).get("updated_at") or 0)
        if cloud.get("deleted"):
            # tombstone wins unless local was written later (still pending push)
            if cloud_ts > local_ts:
                plan.pull_site_tombstones.append((uuid, cloud_ts))
            continue
        if local_row is None:
            plan.pull_sites.append(cloud)
        elif cloud_ts > local_ts:
            plan.pull_sites.append(cloud)
    return plan


def local_row_to_rest(row: dict, user_id: str) -> dict:
    """Shape/normalize a local sites row for the PostgREST upsert."""
    return {
        "user_id": user_id,
        "site_uuid": row["uuid"],
        "name": str(row.get("name", "")),
        "url": str(row.get("url", "")),
        "method": str(row.get("method", "GET")),
        "timeout_s": float(row.get("timeout_s", 10.0)),
        "expected_status": int(row.get("expected_status", 200)),
        "keyword": str(row.get("keyword", "")),
        "enabled": bool(row.get("enabled")),
        "deleted": bool(row.get("deleted")),
        "updated_at": _iso_now(),
    }
