"""Context-tagged money ledger; no ambiguous resMoney totals."""
import uuid
from .domain import ValidationError, only_keys, money, currency


class MoneyLedger:
    def __init__(self,store):
        self.store=store

    def save(self,record):
        try:
            only_keys(record,{"source","context","resMoney"},"money record")
            if record.get("source")!="test_fixture" or record.get("context") not in {"auto_selection","inspection"}:
                raise ValidationError("Explicit fixture business context required")
            tagged=record.get("resMoney")
            only_keys(tagged,{"amount","currency","context"},"resMoney")
            if tagged.get("context")!=record["context"]:
                raise ValidationError("Money context mismatch")
            amount=f"{money(tagged.get('amount'),'amount'):.2f}"
            cur=currency(tagged.get("currency"))
        except (ValidationError,TypeError):
            with self.store.transaction() as db:
                self.store.event(db,None,"money_schema_rejected")
            raise ValidationError("MONEY_CURRENCY_AND_CONTEXT_REQUIRED") from None
        ident=uuid.uuid4().hex
        with self.store.transaction() as db:
            db.execute("INSERT INTO money_records VALUES (?,?,?,?,?,?)",(ident,record["context"],amount,cur,"test_fixture",self.store.clock()))
            self.store.event(db,None,"money_record_committed",context=record["context"],currency=cur)
        return {"id":ident,"context":record["context"],"amount":amount,"currency":cur,"source":"test_fixture"}

    def list(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,context,amount,currency,source FROM money_records ORDER BY created,id")]
