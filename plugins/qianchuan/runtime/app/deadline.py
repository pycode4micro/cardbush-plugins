"""Durable, at-most-once pause submission. No model or strategy scheduling."""
import time
from datetime import datetime

from sqlalchemy import select, update

from app.models import DeliveryDeadline, DeadlineHeartbeat


def worker_status(factory):
    with factory() as db:
        row = db.get(DeadlineHeartbeat, 1)
        age = int(time.time()) - row.checked_epoch if row else None
        return {"online": age is not None and age < 90, "heartbeat_age_seconds": age}


def enforce_deadline(db, action_code, payload):
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if not isinstance(payload, dict):
        return
    if action_code == "tool.qianchuan_uni_promotion_ad_status_update_v1" and payload.get("opt_status") == "DISABLE":
        return
    ids = set()
    def collect(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "ad_id":
                    ids.add(str(v))
                elif k in {"ad_ids", "ad_id_list"} and isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            collect(item)
                        else:
                            ids.add(str(item))
                elif isinstance(v, (list, dict)):
                    collect(v)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)
    collect(payload)
    if ids and db.scalar(select(DeliveryDeadline.id).where(
        DeliveryDeadline.ad_id.in_(ids), DeliveryDeadline.end_epoch <= int(time.time())
    ).limit(1)) is not None:
        from app.strategy import ValidationError
        raise ValidationError("Delivery deadline reached; only terminal DISABLE is permitted for this plan.")


def register(factory, advertiser_id, ad_ids, ends_at):
    deadline = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("ends_at must include timezone, e.g. +08:00")
    now = int(time.time())
    end = int(deadline.timestamp())
    if not now < end <= now + 7 * 86400:
        raise ValueError("Deadline must be in the next seven days")
    if not ad_ids or len(ad_ids) > 100 or any(not str(i).isdigit() for i in ad_ids):
        raise ValueError("Provide 1-100 exact numeric ad IDs")
    with factory() as db:
        for ad_id in set(map(str, ad_ids)):
            row = db.scalar(select(DeliveryDeadline).where(DeliveryDeadline.ad_id == ad_id))
            if row:
                if row.advertiser_id == str(advertiser_id) and row.end_epoch == end:
                    continue
                raise ValueError("Plan already registered; cannot silently replace its deadline")
            db.add(DeliveryDeadline(advertiser_id=str(advertiser_id), ad_id=ad_id,
                                    end_epoch=end, state="scheduled", updated_epoch=now))
        db.commit()


def snapshot(factory):
    with factory() as db:
        return [{"advertiser_id": r.advertiser_id, "ad_id": r.ad_id,
                 "end_epoch": r.end_epoch, "state": r.state,
                 "detail": r.detail, "updated_epoch": r.updated_epoch}
                for r in db.scalars(select(DeliveryDeadline).order_by(DeliveryDeadline.id.desc()).limit(200))]


def stop_now(factory, ad_ids):
    with factory() as db:
        db.execute(update(DeliveryDeadline).where(
            DeliveryDeadline.ad_id.in_(list(map(str, ad_ids))),
            DeliveryDeadline.state == "scheduled",
        ).values(end_epoch=int(time.time()), updated_epoch=int(time.time())))
        db.commit()


def tick(factory, read_status, pause, now=None):
    now = int(time.time()) if now is None else now
    with factory() as db:
        rows = list(db.scalars(select(DeliveryDeadline).where(
            DeliveryDeadline.end_epoch <= now,
            DeliveryDeadline.state.in_(["scheduled", "submitted", "attention"]))))
    for row in rows:
        try:
            status = read_status(row.advertiser_id, row.ad_id)
            if status == "DISABLE":
                _save(factory, row.id, "paused", "Official pause confirmed", now)
                continue
            if status != "ENABLE":
                raise ValueError("Official state is unknown; no write submitted")
            if row.state != "scheduled":
                # Includes crash/timeout after durable submission intent: read only.
                _save(factory, row.id, "attention", "Pause unconfirmed; no automatic resubmission", now)
                continue
            with factory() as db:
                claimed = db.execute(update(DeliveryDeadline).where(
                    DeliveryDeadline.id == row.id, DeliveryDeadline.state == "scheduled"
                ).values(state="submitted", updated_epoch=now)).rowcount
                db.commit()
            if not claimed:
                continue
            pause(row.advertiser_id, row.ad_id)
            if read_status(row.advertiser_id, row.ad_id) == "DISABLE":
                _save(factory, row.id, "paused", "Official pause confirmed", now)
            else:
                _save(factory, row.id, "attention", "Pause unconfirmed; read-only reconciliation pending", now)
        except Exception:
            # Do not store upstream response bodies, tokens or secrets.
            with factory() as db:
                current = db.get(DeliveryDeadline, row.id)
                if current and current.state == "scheduled":
                    current.detail = "Read failed; will retry read"
                elif current and current.state != "paused":
                    current.state = "attention"
                    current.detail = "Pause failed or unknown; inspect action audit; no automatic resubmission"
                if current:
                    current.updated_epoch = now
                    db.commit()


def _save(factory, row_id, state, detail, now):
    with factory() as db:
        db.execute(update(DeliveryDeadline).where(DeliveryDeadline.id == row_id).values(
            state=state, detail=detail, updated_epoch=now))
        db.commit()


def run_once(settings, engine, factory):
    with factory() as db:
        db.merge(DeadlineHeartbeat(id=1, checked_epoch=int(time.time())))
        db.commit()
    # Reuse the MCP's exact guarded write path, policy checks and audit trail.
    from types import SimpleNamespace
    from app.mcp_server import McpAppContext, _strategy_service, qianchuan_update_uni_promotion_ad_status
    ctx = SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=McpAppContext(settings, engine, factory)))
    def read(advertiser, ad):
        result = _strategy_service(ctx).get_uni_promotion_ad_detail(
            {"advertiser_id": int(advertiser), "ad_id": int(ad)})
        data = result.get("data", {})
        if str(data.get("ad_id")) != ad:
            raise ValueError("Plan identity mismatch")
        return data.get("opt_status")
    def pause(advertiser, ad):
        qianchuan_update_uni_promotion_ad_status(ctx,
            payload={"advertiser_id": int(advertiser), "ad_ids": [int(ad)], "opt_status": "DISABLE"},
            confirm=True, reason="用户已授权限时投放任务，到期执行暂停并回读确认")
    tick(factory, read, pause)
