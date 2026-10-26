"""Local fixture sessions only. Never accepts or stores real credentials."""
from .domain import ValidationError
from .store import AuthRequired


class SessionGuard:
    def __init__(self, store):
        self.store = store

    def state(self, account_id="fixture-default"):
        self.store.validate_account(account_id)
        with self.store.connect() as db:
            row = db.execute("SELECT epoch,state FROM sessions WHERE account_id=?", (account_id,)).fetchone()
            return {"account_id":account_id,"epoch":row["epoch"] if row else 0,
                    "state":row["state"] if row else "unauthenticated", "source":"test_fixture",
                    "real_credentials_stored":False, "real_authentication":False}

    def fake_login(self, account_id, source):
        self.store.validate_account(account_id)
        if source != "test_fixture":
            raise ValidationError("Only explicit fixture sessions are enabled")
        with self.store.transaction() as db:
            row = db.execute("SELECT epoch FROM sessions WHERE account_id=?", (account_id,)).fetchone()
            epoch = (row[0] if row else 0) + 1
            db.execute("INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET epoch=excluded.epoch,state=excluded.state,updated=excluded.updated", (account_id,epoch,"authenticated_fixture",self.store.clock()))
            self.store.event(db,None,"fixture_session_authenticated",account_id=account_id,epoch=epoch)
        return self.state(account_id)

    def require(self, account_id="fixture-default"):
        self.store.validate_account(account_id)
        try:
            with self.store.connect() as db:
                self.store.check_session(db, account_id, restricted=True)
        except AuthRequired:
            with self.store.transaction() as db:
                self.store.event(db,None,"login_required",account_id=account_id)
            raise
        return self.state(account_id)

    def _cancel_account(self, db, account_id):
        rows = db.execute("SELECT id FROM tasks WHERE account_id=? AND state NOT IN ('completed','completed_with_errors','canceled')", (account_id,)).fetchall()
        for row in rows:
            db.execute("UPDATE items SET state='canceled',generation=generation+1 WHERE task_id=? AND state IN ('pending','running','retry_wait')", (row[0],))
            db.execute("UPDATE tasks SET state='canceled',generation=generation+1,updated=? WHERE id=?", (self.store.clock(),row[0]))
            self.store.event(db,row[0],"task_auth_canceled")
        db.execute("UPDATE schedules SET state='canceled' WHERE account_id=? AND state='pending'", (account_id,))
        db.execute("UPDATE daily_schedules SET state='canceled' WHERE account_id=? AND state='active'", (account_id,))

    def response(self, account_id, epoch, code):
        self.store.validate_account(account_id)
        if type(epoch) is not int or code not in {0,401,402} or type(code) is not int:
            raise ValidationError("Unsupported synthetic auth response")
        with self.store.transaction() as db:
            row = db.execute("SELECT epoch,state FROM sessions WHERE account_id=?", (account_id,)).fetchone()
            if not row or epoch != row["epoch"] or row["state"] != "authenticated_fixture":
                self.store.event(db,None,"old_session_response_ignored",account_id=account_id)
                return {"ignored":True, "new_work_allowed":False}
            if code in {401,402}:
                db.execute("UPDATE sessions SET epoch=epoch+1,state='expired',updated=? WHERE account_id=?", (self.store.clock(),account_id))
                self._cancel_account(db,account_id)
                self.store.event(db,None,"auth_expired",account_id=account_id)
                return {"expired":True,"new_work_allowed":False}
            # A successful response never authenticates a new session or clears expiry.
            return {"ignored":False,"new_work_allowed":True}

    def logout(self, account_id, remote_confirmed=False):
        self.store.validate_account(account_id)
        # No remote transport exists. Even a caller's assertion cannot confirm logout.
        with self.store.transaction() as db:
            row = db.execute("SELECT epoch FROM sessions WHERE account_id=?", (account_id,)).fetchone()
            epoch = (row[0] if row else 0) + 1
            db.execute("INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET epoch=excluded.epoch,state=excluded.state,updated=excluded.updated", (account_id,epoch,"unauthenticated",self.store.clock()))
            self._cancel_account(db,account_id)
            self.store.event(db,None,"local_logout",account_id=account_id,remote_logout="unconfirmed")
        return {**self.state(account_id),"local_credentials_present":False,"remote_logout":"unconfirmed"}
