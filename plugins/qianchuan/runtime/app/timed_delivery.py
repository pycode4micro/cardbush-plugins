"""Single-call timed enable workflow. No caller-maintained protocol/state required."""
from sqlalchemy.exc import IntegrityError

from app.models import DeliveryStart
from app.deadline import snapshot


def enable_once(factory, ad_ids, read_status, enable):
    results = []
    for ad in map(str, ad_ids):
        # Commit intent before I/O. Unknown outcomes cannot trigger another write.
        with factory() as db:
            try:
                db.add(DeliveryStart(ad_id=ad, state="submitted"))
                db.commit()
                claimed = True
            except IntegrityError:
                db.rollback()
                claimed = False
        try:
            status = read_status(ad)
            if status == "DISABLE" and claimed:
                enable(ad)
                status = read_status(ad)
            state = "running" if status == "ENABLE" else "attention"
        except Exception:
            state = "attention"
        with factory() as db:
            row = db.get(DeliveryStart, ad)
            row.state = state
            db.commit()
        results.append({"ad_id": ad, "state": state, "new_submission_claimed": claimed})
    return results
