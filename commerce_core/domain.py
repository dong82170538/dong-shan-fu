"""Pure normalization, explainable filters and explicit-currency price rules.

These are independently designed policies, not compatibility claims about Shopbang.
All money enters as decimal strings (never binary floats).
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import json
import re


class ValidationError(ValueError):
    pass


CURRENCIES = {"CNY", "RUB", "USD"}
SECRET_KEY = re.compile(r"token|password|secret|cookie|authorization|credential|api.?key", re.I)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def integer(value, name, minimum=0, maximum=1_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{name}: expected integer in [{minimum}, {maximum}]")
    return value


def decimal(value, name, positive=False):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValidationError(f"{name}: use a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{name}: invalid decimal") from None
    if not result.is_finite() or result < 0 or result > Decimal("1000000000"):
        raise ValidationError(f"{name}: out of range")
    if positive and result <= 0:
        raise ValidationError(f"{name}: must be positive")
    if result.as_tuple().exponent < -8:
        raise ValidationError(f"{name}: maximum 8 decimal places")
    return result


def money(value, name):
    result = decimal(value, name)
    if result != result.quantize(Decimal("0.01")):
        raise ValidationError(f"{name}: maximum 2 decimal places")
    return result


def text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{name}: expected nonempty text (max {limit})")
    return " ".join(value.split())


def currency(value):
    if not isinstance(value, str) or value not in CURRENCIES:
        raise ValidationError("currency: supported values CNY, RUB, USD")
    return value


def only_keys(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValidationError(f"{name}: unknown or sensitive fields are not accepted")


def validate_fixture(fixture):
    only_keys(fixture, {"schema_version", "source", "name", "products"}, "fixture")
    if type(fixture.get("schema_version")) is not int or fixture.get("schema_version") != 1 or fixture.get("source") != "test_fixture":
        raise ValidationError("Only schema_version=1, source=test_fixture imports are enabled")
    text(fixture.get("name"), "fixture name", 80)
    products = fixture.get("products")
    if not isinstance(products, list) or not 1 <= len(products) <= 5000:
        raise ValidationError("products: expected 1..5000 items")
    seen = {}
    for product in products:
        only_keys(product, {"external_id", "platform", "title", "category", "skus", "test_behavior", "selection"}, "product")
        ident = text(product.get("external_id"), "external_id", 80)
        platform = product.get("platform")
        if platform not in {"OZON", "WB"}:
            raise ValidationError("fixture platform must be OZON or WB")
        if (platform, ident) in seen and seen[(platform, ident)] != canonical(product):
            raise ValidationError("conflicting duplicate product identity in fixture")
        seen[(platform, ident)] = canonical(product)
        text(product.get("title"), "title")
        text(product.get("category", "Uncategorized"), "category", 80)
        if "selection" in product:
            only_keys(product["selection"], {"price", "currency", "sold_count"}, "selection")
            money(product["selection"].get("price"), "selection price")
            currency(product["selection"].get("currency"))
            integer(product["selection"].get("sold_count"), "sold_count")
        skus = product.get("skus")
        if not isinstance(skus, list) or not 1 <= len(skus) <= 100:
            raise ValidationError("skus: expected 1..100 entries")
        sku_ids = set()
        for sku in skus:
            only_keys(sku, {"sku", "title", "stock", "cost", "currency", "original_price"}, "sku")
            ident = text(sku.get("sku"), "sku", 80).upper()
            if ident in sku_ids:
                raise ValidationError("duplicate normalized SKU")
            sku_ids.add(ident)
            text(sku.get("title", ident), "SKU title")
            integer(sku.get("stock"), "stock")
            currency(sku.get("currency"))
            money(sku.get("cost"), "cost")
            money(sku.get("original_price", sku.get("cost")), "original_price")
        behavior = product.get("test_behavior", {})
        only_keys(behavior, {"delay_ms", "fail_attempts", "save_fail_attempts"}, "test_behavior")
        integer(behavior.get("delay_ms", 0), "delay_ms", 0, 5000)
        integer(behavior.get("fail_attempts", 0), "fail_attempts", 0, 10)
        integer(behavior.get("save_fail_attempts", 0), "save_fail_attempts", 0, 10)
    return fixture


DEFAULT_POLICY = {
    "selection_profile": "stock_cost", "min_sold_count": 151,
    "pricing_profile": "cost_plus_v1", "sale_factor_bps": 9000, "strike_factor_bps": 16000,
    "surface": "desktop", "min_stock": 1, "banned_terms": [],
    "cost_currency": "CNY", "min_cost": "0.00", "max_cost": "1000000.00",
    "settlement_currency": "RUB", "fx_rate": "12.5",
    "fee_bps": 1000, "margin_bps": 2000, "fixed_fee": "10.00", "strike_markup_bps": 3000,
}


def validate_policy(raw=None):
    raw = {} if raw is None else raw
    only_keys(raw, DEFAULT_POLICY, "policy")
    policy = {**DEFAULT_POLICY, **raw}
    if policy["selection_profile"] not in {"stock_cost", "fixture_threshold_v1"}:
        raise ValidationError("unknown selection_profile")
    integer(policy["min_sold_count"], "min_sold_count")
    if policy["pricing_profile"] not in {"cost_plus_v1", "mock_fee_v1"}:
        raise ValidationError("unknown pricing_profile")
    integer(policy["sale_factor_bps"], "sale_factor_bps", 1, 100000)
    integer(policy["strike_factor_bps"], "strike_factor_bps", 1, 100000)
    if policy["surface"] not in {"desktop", "extension"}:
        raise ValidationError("surface: desktop or extension")
    integer(policy["min_stock"], "min_stock")
    currency(policy["cost_currency"])
    currency(policy["settlement_currency"])
    if money(policy["min_cost"], "min_cost") > money(policy["max_cost"], "max_cost"):
        raise ValidationError("min_cost exceeds max_cost")
    rate = decimal(policy["fx_rate"], "fx_rate", positive=True)
    if policy["cost_currency"] == policy["settlement_currency"] and rate != 1:
        raise ValidationError("same-currency fx_rate must be 1")
    integer(policy["fee_bps"], "fee_bps", 0, 9999)
    integer(policy["margin_bps"], "margin_bps", 0, 9999)
    if policy["fee_bps"] + policy["margin_bps"] >= 10000:
        raise ValidationError("fee_bps + margin_bps must be below 10000")
    money(policy["fixed_fee"], "fixed_fee")
    integer(policy["strike_markup_bps"], "strike_markup_bps", 0, 100000)
    terms = policy["banned_terms"]
    if not isinstance(terms, list) or len(terms) > 30:
        raise ValidationError("banned_terms: expected up to 30 terms")
    policy["banned_terms"] = [text(t, "banned_term", 50).casefold() for t in terms]
    # Canonicalize money/rate fields so semantically equal policies hash equally.
    for key in ("min_cost", "max_cost", "fixed_fee"):
        policy[key] = f"{money(policy[key], key):.2f}"
    policy["fx_rate"] = format(rate.normalize(), "f")
    return policy


def quote(sku, policy):
    if sku["currency"] != policy["cost_currency"]:
        raise ValidationError("currency mismatch: an explicit matching FX pair is required")
    cost = money(sku["cost"], "cost")
    rate = decimal(policy["fx_rate"], "fx_rate", positive=True)
    fixed = money(policy["fixed_fee"], "fixed_fee")
    denominator = 1 - Decimal(policy["fee_bps"] + policy["margin_bps"]) / 10000
    sale = ((cost * rate + fixed) / denominator).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    # Explicit independent policies, never interchangeable across surfaces.
    base = sale if policy["surface"] == "desktop" else money(sku["original_price"], "original_price") * rate
    strike = (base * (1 + Decimal(policy["strike_markup_bps"]) / 10000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if policy["pricing_profile"] == "mock_fee_v1":
        sale = (cost * rate * Decimal(policy["sale_factor_bps"]) / 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        mock_base = cost * rate if policy["surface"] == "desktop" else sale
        strike = (mock_base * Decimal(policy["strike_factor_bps"]) / 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if sale > Decimal("1000000000") or strike > Decimal("1000000000"):
        raise ValidationError("calculated price out of range")
    return {
        "cost": {"amount": f"{cost:.2f}", "currency": sku["currency"]},
        "sale": {"amount": f"{sale:.2f}", "currency": policy["settlement_currency"]},
        "strike": {"amount": f"{strike:.2f}", "currency": policy["settlement_currency"]},
        "fx": {"from": sku["currency"], "to": policy["settlement_currency"], "rate": policy["fx_rate"], "source": "user_fixture_rate"},
        "rule_version": "mock_fee_" + policy["surface"] + "_v1" if policy["pricing_profile"] == "mock_fee_v1" else "cost_plus_" + policy["surface"] + "_v1",
        "strike_basis": ("synthetic_fee_converted" if policy["surface"] == "desktop" else "calculated_sale") if policy["pricing_profile"] == "mock_fee_v1" else ("calculated_sale" if policy["surface"] == "desktop" else "source_original_converted"),
        "formula": "mock_fee * fx_rate * factor_bps/10000" if policy["pricing_profile"] == "mock_fee_v1" else "ceil_cent((cost * fx_rate + fixed_fee) / (1 - fee_bps/10000 - margin_bps/10000))",
        "fee_quote": {"amount": f"{cost:.2f}", "currency": sku["currency"], "source": "synthetic_fee_adapter", "provenance": "fictional_mock_not_fee_computation", "context": "auto_selection"} if policy["pricing_profile"] == "mock_fee_v1" else None,
        "sale_factor_bps": policy["sale_factor_bps"], "strike_factor_bps": policy["strike_factor_bps"],
        "fee_bps": policy["fee_bps"], "margin_bps": policy["margin_bps"], "fixed_fee": policy["fixed_fee"],
        "strike_markup_bps": policy["strike_markup_bps"],
        "warnings": ["strike_below_sale"] if strike < sale else [],
    }


def normalize_product(raw):
    return {
        "id": hashlib.sha256((raw["platform"] + ":" + text(raw["external_id"], "external_id", 80)).encode()).hexdigest()[:24],
        "source": "test_fixture", "platform": raw["platform"],
        "external_id": text(raw["external_id"], "external_id", 80),
        "title": text(raw["title"], "title"), "category": text(raw.get("category", "Uncategorized"), "category", 80),
        "skus": [{
            "sku": text(s["sku"], "sku", 80).upper(), "title": text(s.get("title", s["sku"]), "SKU title"),
            "stock": s["stock"], "currency": s["currency"], "cost": f"{money(s['cost'], 'cost'):.2f}",
            "original_price": f"{money(s.get('original_price', s['cost']), 'original_price'):.2f}",
        } for s in raw["skus"]],
    }


def evaluate(raw, policy):
    product = normalize_product(raw)
    for sku in product["skus"]:
        reasons = []
        if policy["selection_profile"] == "fixture_threshold_v1":
            selection = raw.get("selection")
            if selection is None:
                reasons.append("selection_data_missing")
            elif selection["sold_count"] < policy["min_sold_count"]:
                reasons.append("rejected_soldCount")
        haystack = (product["title"] + " " + sku["title"]).casefold()
        if any(term in haystack for term in policy["banned_terms"]):
            reasons.append("banned_term")
        if sku["stock"] < policy["min_stock"]:
            reasons.append("stock_below_minimum")
        if sku["currency"] != policy["cost_currency"]:
            reasons.append("currency_pair_missing")
        elif not money(policy["min_cost"], "min_cost") <= money(sku["cost"], "cost") <= money(policy["max_cost"], "max_cost"):
            reasons.append("cost_outside_range")
        sku["decision"] = "filtered" if reasons else "accepted"
        sku["reasons"] = reasons or ["all_rules_passed"]
        sku["price"] = None if reasons else quote(sku, policy)
    product["accepted_skus"] = sum(s["decision"] == "accepted" for s in product["skus"])
    return product


def validate_result(result, raw, policy):
    """Reject malformed/foreign adapter results before the persistence boundary."""
    only_keys(result, {"id", "source", "platform", "external_id", "title", "category", "skus", "accepted_skus"}, "adapter result")
    normalized = normalize_product(raw)
    for key in ("id", "source", "platform", "external_id"):
        if result.get(key) != normalized[key]:
            raise ValidationError("adapter result identity mismatch")
    text(result.get("title"), "result title")
    text(result.get("category"), "result category", 80)
    skus = result.get("skus")
    if not isinstance(skus, list) or len(skus) != len(normalized["skus"]):
        raise ValidationError("adapter result SKU set mismatch")
    seen = set()
    allowed_reasons = {"all_rules_passed", "banned_term", "stock_below_minimum", "currency_pair_missing", "cost_outside_range", "selection_data_missing", "rejected_soldCount"}
    for sku in skus:
        only_keys(sku, {"sku", "title", "stock", "currency", "cost", "original_price", "decision", "reasons", "price"}, "adapter SKU")
        ident = text(sku.get("sku"), "result SKU", 80)
        if ident in seen:
            raise ValidationError("adapter result duplicate SKU")
        seen.add(ident)
        text(sku.get("title"), "result SKU title")
        integer(sku.get("stock"), "result stock")
        currency(sku.get("currency"))
        money(sku.get("cost"), "result cost")
        money(sku.get("original_price"), "result original_price")
        reasons = sku.get("reasons")
        if not isinstance(reasons, list) or not reasons or any(not isinstance(r, str) or r not in allowed_reasons for r in reasons):
            raise ValidationError("adapter result reasons invalid")
        if sku.get("decision") == "accepted":
            if reasons != ["all_rules_passed"] or sku.get("price") != quote(sku, policy):
                raise ValidationError("adapter accepted price contract mismatch")
        elif sku.get("decision") != "filtered" or sku.get("price") is not None or "all_rules_passed" in reasons:
            raise ValidationError("adapter decision contract mismatch")
    if seen != {s["sku"] for s in normalized["skus"]}:
        raise ValidationError("adapter result SKU identities mismatch")
    count = integer(result.get("accepted_skus"), "accepted_skus", 0, 100)
    if count != sum(s["decision"] == "accepted" for s in skus):
        raise ValidationError("adapter result count mismatch")
    # For the only enabled adapter, fixture inputs and core rules are authoritative.
    # Adapters may delay/fail, but cannot rewrite identity, costs or filter decisions.
    if result != evaluate(raw, policy):
        raise ValidationError("adapter result differs from trusted fixture evaluation")


def check_range(value, lower=None, upper=None, profile="strict_v1"):
    """Explicit numeric missing-value profiles, separate from monetary validation.

    legacy_static_v1 characterizes the supplied static rule only. Production money
    imports still require complete currency-tagged decimal strings.
    """
    if profile not in {"strict_v1", "legacy_static_v1"}:
        raise ValidationError("Unknown range profile")
    if lower is None and upper is None:
        return True
    if value is None:
        if profile == "strict_v1":
            return False
        value = 0
    try:
        number = Decimal(str(value))
        minimum = Decimal(str(lower)) if lower is not None else None
        maximum = Decimal(str(upper)) if upper is not None else None
        if not number.is_finite() or any(bound is not None and not bound.is_finite() for bound in (minimum, maximum)):
            return False
        return (minimum is None or number >= minimum) and (maximum is None or number <= maximum)
    except (InvalidOperation, ValueError, TypeError):
        return False
