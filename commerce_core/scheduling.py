"""Durable daily local-time schedules; missed occurrences are recorded and skipped."""
from datetime import datetime, date, time, timedelta, timezone
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import ValidationError, canonical, validate_policy


def next_due(after, zone_name, local_time):
    try:
        zone=ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError,TypeError,ValueError):
        raise ValidationError("Unknown IANA timezone") from None
    if not isinstance(local_time,str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",local_time):
        raise ValidationError("Daily time must be HH:MM")
    hour,minute=map(int,local_time.split(":"))
    local=datetime.fromtimestamp(after,timezone.utc).astimezone(zone)
    day=local.date()
    for _ in range(3):
        candidate=datetime.combine(day,time(hour,minute),zone)
        timestamp=candidate.timestamp()
        # Reject ambiguous/nonexistent clock times; do not invent a DST policy.
        alternate=candidate.replace(fold=1)
        if max(timestamp,alternate.timestamp())<=after:
            day+=timedelta(days=1)
            continue
        roundtrip=datetime.fromtimestamp(timestamp,zone)
        if roundtrip.replace(tzinfo=None)!=candidate.replace(tzinfo=None) or alternate.utcoffset()!=candidate.utcoffset():
            raise ValidationError("Ambiguous/nonexistent local occurrence requires an explicit DST policy")
        if timestamp>after:
            return timestamp
        day+=timedelta(days=1)
    raise ValidationError("Cannot resolve next occurrence")


class DailyScheduler:
    def __init__(self,store):
        self.store=store

    def create(self,fixture_id,policy,zone_name,local_time,account_id="fixture-default"):
        self.store.validate_account(account_id)
        policy=validate_policy(policy)
        due=next_due(self.store.clock(),zone_name,local_time)
        ident=uuid.uuid4().hex
        with self.store.transaction() as db:
            self.store.check_session(db,account_id)
            if not db.execute("SELECT 1 FROM fixtures WHERE id=?",(fixture_id,)).fetchone():
                raise ValidationError("Fixture does not exist")
            db.execute("INSERT INTO daily_schedules VALUES (?,?,?,?,?,?,?,'active')",(ident,account_id,zone_name,local_time,due,fixture_id,canonical(policy)))
            self.store.event(db,None,"daily_schedule_created",schedule_id=ident,timezone=zone_name,local_time=local_time)
        return {"id":ident,"next_due":due,"timezone":zone_name,"local_time":local_time,"state":"active"}

    def _advance(self,db,recovery):
        now=self.store.clock()
        for row in db.execute("SELECT * FROM daily_schedules WHERE state='active' AND next_due<=?",(now,)).fetchall():
            due=row["next_due"]
            for _ in range(10000):
                if due>now:
                    break
                missed=recovery or now-due>2
                existing=db.execute("SELECT 1 FROM occurrences WHERE schedule_id=? AND due=?",(row["id"],due)).fetchone()
                if not existing:
                    task_id=None
                    if not missed:
                        policy=json.loads(row["policy"])
                        key="daily/"+row["id"]+"/"+str(int(due))
                        digest=hashlib.sha256(canonical([row["fixture_id"],policy,due,row["account_id"]]).encode()).hexdigest()
                        task_id=self.store._submit(db,row["fixture_id"],policy,key,digest,2000,row["account_id"])["id"]
                    state="missed" if missed else "submitted"
                    db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?)",(row["id"],due,state,task_id,now))
                    self.store.event(db,task_id,"schedule_missed" if missed else "schedule_occurrence_submitted",schedule_id=row["id"],due_utc=due)
                try:
                    due=next_due(due,row["timezone"],row["local_time"])
                except ValidationError:
                    db.execute("UPDATE daily_schedules SET state='needs_attention' WHERE id=?",(row["id"],))
                    self.store.event(db,None,"daily_dst_policy_required",schedule_id=row["id"])
                    break
            else:
                db.execute("UPDATE daily_schedules SET state='needs_attention' WHERE id=?",(row["id"],))
                self.store.event(db,None,"daily_recovery_limit",schedule_id=row["id"])
            db.execute("UPDATE daily_schedules SET next_due=? WHERE id=?",(due,row["id"]))

    def recover(self,db):
        self._advance(db,True)

    def tick(self,db):
        self._advance(db,False)

    def list(self):
        with self.store.connect() as db:
            return [{**dict(row),"occurrences":[dict(r) for r in db.execute("SELECT due,state,task_id,recorded FROM occurrences WHERE schedule_id=? ORDER BY due",(row["id"],))]} for row in db.execute("SELECT id,account_id,timezone,local_time,next_due,state FROM daily_schedules ORDER BY next_due")]

    def cancel(self,ident):
        with self.store.transaction() as db:
            row=db.execute("SELECT state FROM daily_schedules WHERE id=?",(ident,)).fetchone()
            if not row:raise KeyError(ident)
            db.execute("UPDATE daily_schedules SET state='canceled' WHERE id=?",(ident,))
            if row[0]!="canceled":self.store.event(db,None,"daily_schedule_canceled",schedule_id=ident)
        return {"id":ident,"state":"canceled"}
