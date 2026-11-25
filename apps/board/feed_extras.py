"""Real-evidence helpers for 7-day backup trend and read-only SMB directory browser.

Strict rules:
- Never fabricate historical points; compute past days exclusively from verified
  published receipts on mounted NAS shares (or persisted daily real snapshots).
- Never expose credentials, never follow symlinks, and cap directory depth/counts.
"""
import datetime as dt
import itertools
import json
import re
from pathlib import Path

BJ_TZ = dt.timezone(dt.timedelta(hours=8))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_RE = re.compile(r"^(?:\d{8}T\d{6}|RMAN\d{8}|\d{8})")
RECEIPT_NAMES = ("complete.json", "backup-result.json", "hotcopy-result.json", "pending.json")

SHARE_DISPLAY = {
    "nas243": {
        "name": "NAS 243",
        "unc": r"\\10.10.11.243\资源备份",
        "role": "研发备份存储",
    },
    "win254": {
        "name": "钱总办公室",
        "unc": r"\\10.10.10.254\资源备份",
        "role": "钱总办公室",
    },
    "nas240": {
        "name": "NAS 240",
        "unc": r"\\192.168.5.240\资源备份",
        "role": "研发备份存储",
    },
}


def _read_small_json(path: Path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return None
        val = json.loads(path.read_text(encoding="utf-8-sig"))
        return val if isinstance(val, dict) else None
    except (OSError, ValueError):
        return None


def _dir_has_valid_receipt(day_dir: Path) -> bool:
    """Return True iff day_dir (date folder or a non-partial run subfolder) has a valid non-failed receipt."""
    if day_dir.is_symlink() or not day_dir.is_dir() or ".partial" in day_dir.name:
        return False

    candidates = [day_dir]
    try:
        for child in itertools.islice(day_dir.iterdir(), 60):
            if not child.is_symlink() and child.is_dir() and ".partial" not in child.name:
                candidates.append(child)
    except OSError:
        return False

    for folder in candidates:
        for rname in RECEIPT_NAMES:
            r = _read_small_json(folder / rname)
            if not r:
                continue
            if r.get("SmallTest") is True:
                continue
            status = str(r.get("Status", r.get("status", ""))).lower()
            if status in ("failed", "failure", "error"):
                continue
            return True
    return False


def _format_mtime(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=BJ_TZ).strftime("%Y/%m/%d %H:%M")


def build_smb_browser(targets: list, now: dt.datetime = None, max_top: int = 80, max_children: int = 8) -> dict:
    """Build a bounded, read-only directory listing across mounted NAS shares."""
    ts = (now or dt.datetime.now(BJ_TZ)).astimezone(BJ_TZ).isoformat(timespec="seconds")
    shares = []
    for t in targets or []:
        tid = t.get("id", "")
        meta = SHARE_DISPLAY.get(tid, {"name": tid or "未知存储", "unc": t.get("path", ""), "role": "备份存储"})
        mount_path = Path(t.get("path", "")) if t.get("path") else None
        mounted = t.get("status") == "mounted" and mount_path is not None and mount_path.is_dir()
        entries = []
        if mounted:
            try:
                raw_items = [e for e in itertools.islice(mount_path.iterdir(), max_top * 2) if not e.is_symlink()]
                raw_items.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
                for item in raw_items[:max_top]:
                    try:
                        st = item.stat()
                    except OSError:
                        continue
                    is_dir = item.is_dir()
                    children = []
                    if is_dir and not item.name.startswith("#"):
                        try:
                            subs = [c for c in itertools.islice(item.iterdir(), 120) if not c.is_symlink()]
                            subs.sort(key=lambda p: p.name, reverse=True)
                            for sub in subs[:max_children]:
                                try:
                                    sst = sub.stat()
                                except OSError:
                                    continue
                                is_partial = ".partial" in sub.name
                                has_rcpt = (not is_partial) and sub.is_dir() and _dir_has_valid_receipt(sub)
                                children.append({
                                    "name": sub.name,
                                    "kind": "dir" if sub.is_dir() else "file",
                                    "mtime": _format_mtime(sst.st_mtime),
                                    "is_partial": is_partial,
                                    "has_receipt": has_rcpt,
                                })
                        except OSError:
                            pass
                    entries.append({
                        "name": item.name,
                        "kind": "dir" if is_dir else "file",
                        "mtime": _format_mtime(st.st_mtime),
                        "size": None if is_dir else st.st_size,
                        "children": children,
                    })
            except OSError:
                mounted = False

        asset_dirs = [e for e in entries if e["kind"] == "dir" and not e["name"].startswith("#")]
        recent_count = sum(1 for e in asset_dirs if any(c.get("has_receipt") for c in e.get("children", [])))
        shares.append({
            "id": tid,
            "name": meta["name"],
            "unc": meta["unc"],
            "role": meta["role"],
            "mounted": mounted,
            "total": t.get("total"),
            "free": t.get("free"),
            "dir_count": len(asset_dirs),
            "recent_verified_dirs": recent_count,
            "healthy": mounted and len(asset_dirs) > 0,
            "entries": entries,
        })
    return {"sampled": ts, "shares": shares}


def build_history_7d(
    snapshot: dict,
    projected_today: dict,
    prior_history: dict = None,
    now: dt.datetime = None,
) -> dict:
    """Compute real 7-day delivery trend from actual NAS receipts + persisted daily live snapshots."""
    ref = (now or dt.datetime.now(BJ_TZ)).astimezone(BJ_TZ)
    today = ref.date()
    dates = [(today - dt.timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]

    # Index any previously saved daily live snapshots (never overwrite real live snapshots with backfill)
    saved_live = {}
    for item in (prior_history or {}).get("days", []):
        if isinstance(item, dict) and item.get("date") and item.get("source") == "live_snapshot":
            saved_live[item["date"]] = item

    assets = snapshot.get("assets") or []
    total_assets = len(assets)
    targets_map = {
        t["id"]: Path(t["path"])
        for t in (snapshot.get("targets") or [])
        if t.get("status") == "mounted" and t.get("path")
    }

    # Pre-scan actual published receipt dates on mounted NASes per asset per target
    asset_target_dates = {}
    for a in assets:
        aid = a.get("id", "")
        asset_dir_name = a.get("asset", "")
        asset_target_dates[aid] = {}
        if not asset_dir_name or Path(asset_dir_name).name != asset_dir_name:
            continue
        for tid in a.get("targets") or []:
            root = targets_map.get(tid)
            if not root:
                continue
            base = root / asset_dir_name
            if not base.is_dir() or base.is_symlink():
                continue
            valid_dates = set()
            try:
                for entry in itertools.islice(base.iterdir(), 200):
                    if DATE_RE.fullmatch(entry.name) and _dir_has_valid_receipt(entry):
                        valid_dates.add(entry.name)
            except OSError:
                pass
            asset_target_dates[aid][tid] = valid_dates

    today_iso = today.isoformat()
    today_summary = (projected_today or {}).get("summary") or {}
    days_out = []

    for d_iso in dates:
        label = d_iso[5:]  # MM-DD
        if d_iso == today_iso:
            delivered = int(today_summary.get("delivered", 0))
            total = int(today_summary.get("total", total_assets))
            attention = int(today_summary.get("attention", max(0, total - delivered)))
            rate = round((delivered / total) * 100, 1) if total > 0 else 0.0
            days_out.append({
                "date": d_iso,
                "label": label,
                "total": total,
                "delivered": delivered,
                "attention": attention,
                "rate": rate,
                "source": "live_snapshot",
            })
            continue

        if d_iso in saved_live:
            item = dict(saved_live[d_iso])
            item["label"] = label
            days_out.append(item)
            continue

        # Reconstruct from real NAS published receipts using the same per-asset max_age_days window
        d_obj = dt.date.fromisoformat(d_iso)
        delivered_count = 0
        any_evidence = False
        for a in assets:
            expected = a.get("targets") or []
            if not expected:
                continue
            limit = int(a.get("max_age_days", 8 if a.get("type") == "Harbor" else 2))
            aid = a.get("id", "")
            all_targets_ok = True
            for tid in expected:
                valid_set = asset_target_dates.get(aid, {}).get(tid, set())
                if valid_set:
                    any_evidence = True
                # Check if any real receipt falls within [d_obj - limit, d_obj]
                hit = False
                for vd in valid_set:
                    delta = (d_obj - dt.date.fromisoformat(vd)).days
                    if 0 <= delta <= limit:
                        hit = True
                        break
                if not hit:
                    all_targets_ok = False
                    break
            if all_targets_ok:
                delivered_count += 1

        if any_evidence and total_assets > 0:
            days_out.append({
                "date": d_iso,
                "label": label,
                "total": total_assets,
                "delivered": delivered_count,
                "attention": max(0, total_assets - delivered_count),
                "rate": round((delivered_count / total_assets) * 100, 1),
                "source": "nas_receipts",
            })
        else:
            days_out.append({
                "date": d_iso,
                "label": label,
                "total": total_assets,
                "delivered": None,
                "attention": None,
                "rate": None,
                "source": "no_evidence",
            })

    return {
        "sampled": ref.isoformat(timespec="seconds"),
        "days": days_out,
    }
