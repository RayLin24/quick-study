"""Provision a throwaway E2E test account + session (additive only)."""
import json
import sys

sys.path.insert(0, "apps/api")

from app.auth.accounts import create_user
from app.auth.sessions import create_session
from app.db.session import get_session_factory

EMAIL = "dsh-e2e@local.test"
PASSWORD = "E2eTest-Passw0rd!"

factory = get_session_factory()
with factory() as session:
    user = create_user(session, email=EMAIL, password=PASSWORD)
    session.commit()
    issued = create_session(session, user, user_agent="dsh-e2e")
    session.commit()
    print(json.dumps({"user_id": user.id, "token": issued.token, "csrf": issued.csrf_token}))
