"""Independent fixture SKU preview rules; no extension host or publishing."""
from decimal import Decimal, ROUND_HALF_UP
from .domain import ValidationError, only_keys, money, text


def preview(body):
    only_keys(body,{"source","candidate_origin","publish","selected_skus"},"extension preview")
    if body.get("source")!="test_fixture" or body.get("candidate_origin")!="https://fixture.invalid" or body.get("publish") is not False:
        raise ValidationError("Only explicit offline extension fixture previews are enabled")
    rows=body.get("selected_skus")
    if not isinstance(rows,list) or not 1<=len(rows)<=100:
        raise ValidationError("Expected selected_skus")
    errors=[];seen=set();goods=set();normalized=[]
    for row in rows:
        only_keys(row,{"goods_id","offer_id","price","old_price","min_price","currency"},"extension SKU")
        ident=text(row.get("goods_id"),"goods_id",80)
        offer=text(row.get("offer_id"),"offer_id",80)
        if ident in goods:errors.append({"goods_id":ident,"code":"duplicate_goods_id"})
        goods.add(ident)
        if offer in seen:errors.append({"goods_id":ident,"code":"duplicate_offer_id"})
        seen.add(offer)
        price=money(row.get("price"),"price")
        old=money(row.get("old_price"),"old_price")
        minimum=money(row.get("min_price"),"min_price")
        floor=(price*Decimal("0.5")).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        if minimum<floor:errors.append({"goods_id":ident,"code":"below_min_price"})
        if minimum>price:errors.append({"goods_id":ident,"code":"above_sale_price"})
        from .domain import currency
        cur=currency(row.get("currency"))
        normalized.append({"goods_id":ident,"offer_id":offer,"price":f"{price:.2f}","old_price":f"{old:.2f}","min_price":f"{minimum:.2f}","currency":cur})
    return {"source":"test_fixture","state":"rejected" if errors else "dry_run_preview","errors":errors,"skus":normalized if not errors else [],"publish_calls":0,"host_integrated":False}
