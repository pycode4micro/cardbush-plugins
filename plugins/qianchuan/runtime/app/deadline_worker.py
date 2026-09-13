"""Run independently of Agent sessions: python -m app.deadline_worker."""
import logging
import time

from app.config import get_settings
from app.db import build_engine, create_session_factory, init_db
from app.deadline import run_once


def main():
    settings = get_settings()
    settings.validate_for_startup()
    engine = build_engine(settings)
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        while True:
            try:
                run_once(settings, engine, factory)
            except Exception:
                logging.exception("Deadline cycle failed")
            time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
