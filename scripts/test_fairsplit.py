"""Financial invariants, realistic scope failures, privacy, persistence and transport."""
import contextlib
import copy
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from fairsplit import BillError, allocate, compare, history, money, save_version, solve, yuan
from cli import main, draft, ROOT
from mcp_readonly import Client, MCPError, safe_order
import render


def example(which="four_people"):
    return json.loads((ROOT/"assets"/"examples"/(which+".json")).read_text())


class MoneyTests(unittest.TestCase):
    def test_decimal_conversion(self):
        self.assertEqual(yuan("0.29"), 29)
        self.assertEqual(yuan("12.3"), 1230)
        self.assertEqual(money(-1), "−0.01")
        for bad in (0.29, "0.291", "NaN", "1e3", "-1", "0.01\n", True):
            with self.assertRaises(BillError):
                yuan(bad)

    def test_small_remainder_and_order_independence(self):
        a = allocate(1, {"A": 1, "B": 1, "C": 1}, "stable")
        b = allocate(1, {"C": 1, "A": 1, "B": 1}, "stable")
        self.assertEqual(a, b)
        self.assertEqual(sum(a.values()), 1)
        self.assertEqual(sorted(a.values()), [0, 0, 1])

    def test_zero_and_missing_weights(self):
        self.assertEqual(allocate(0, {"A": 0}, "s"), {"A": 0})
        for weights in ({"A": 0}, {"A": -1}, {"A": True}, {}):
            with self.assertRaises(BillError):
                allocate(1, weights, "s")

    def test_discount_cap_redistribution(self):
        self.assertEqual(allocate(100, {"A": 1, "B": 1}, "s", {"A": 1, "B": 200}), {"A": 1, "B": 99})
        with self.assertRaises(BillError):
            allocate(202, {"A": 1, "B": 1}, "s", {"A": 1, "B": 200})

    def test_random_capped_conservation_1000_cases(self):
        rng = random.Random(1024)
        for _ in range(1000):
            keys = [str(i) for i in range(rng.randint(2, 10))]
            weights = {p: rng.randint(1, 1000) for p in keys}
            caps = {p: rng.randint(0, 50000) for p in keys}
            amount = rng.randint(0, sum(caps.values()))
            result = allocate(amount, weights, "property", caps)
            self.assertEqual(sum(result.values()), amount)
            self.assertTrue(all(0 <= result[p] <= caps[p] for p in keys))
            self.assertEqual(result, allocate(amount, dict(reversed(list(weights.items()))), "property", caps))


class BillTests(unittest.TestCase):
    def test_reference_bill(self):
        r = solve(example())
        self.assertEqual([p["total_minor"] for p in r["people"]], [3174, 2391, 1000, 2235])
        self.assertEqual(sum(t["amount_minor"] for t in r["transfers"]), 5626)

    def test_comparison_is_pure(self):
        b = example()
        original = copy.deepcopy(b)
        b["organizer_confirmed"] = True
        original["organizer_confirmed"] = True
        r = compare(b)
        self.assertEqual(b, original)
        self.assertEqual(sum(r["delta_minor"].values()), 0)
        self.assertEqual([p["total_minor"] for p in r["alternate"]["people"]], [3333, 2333, 1000, 2134])
        self.assertEqual(r["alternate"]["status"], "ready")

    def test_bundle_and_one_cent(self):
        self.assertEqual([p["total_minor"] for p in solve(example("bundle"))["people"]], [2000, 1000])
        self.assertEqual(sum(p["total_minor"] for p in solve(example("one_cent"))["people"]), 1)

    def test_exact_product_discount_scope(self):
        b = example()
        b["discounts"][0]["eligible_items"] = ["meal-B"]
        r = solve(b)
        self.assertEqual([p["discount_minor"] for p in r["people"]], [0, 2000, 0, 0])
        b["discounts"][0]["eligible_items"] = ["drink-C"]
        with self.assertRaises(BillError):
            solve(b)

    def test_overlapping_discount_caps(self):
        b = example()
        b["discounts"] = [
            {"id":"d1","name":"优惠1","amount_minor":1000,"source":"synthetic_demo","eligible":["A"],"eligible_items":["shared"],"policy":"equal"}]
        b["paid_total_minor"] = b["payer_paid_minor"] = 9800
        with self.assertRaises(BillError):
            solve(b)  # A's share of the shared item is only 600.
        b["discounts"][0]["amount_minor"] = 500
        b["discounts"].append({**b["discounts"][0], "id": "d2", "amount_minor": 500})
        with self.assertRaises(BillError):
            solve(b)  # The first coupon leaves only 100 on that item.

    def test_specified_and_compare_requires_new_agreement(self):
        b = example()
        b["discounts"][0].update(policy="specified", allocations={"A":2000,"B":0,"D":0})
        self.assertEqual(solve(b)["people"][0]["discount_minor"], 2000)
        with self.assertRaises(BillError):
            compare(b)
        b["discounts"][0]["allocations"] = {"A":2001,"B":0,"D":0}
        with self.assertRaises(BillError):
            solve(b)

    def test_reject_unknown_mismatch_unpaid(self):
        changes = [("unresolved", ["未知费用"]), ("paid_total_minor", 8799),
                   ("payment_status", "cancelled"), ("rules_confirmed", False),
                   ("schema_version", True), ("refunds", [{"amount_minor":100}]),
                   ("currency", "USD"), ("payer_paid_minor", 100)]
        for key, value in changes:
            b = example()
            b[key] = value
            with self.subTest(key=key), self.assertRaises(BillError):
                solve(b)

    def test_claim_validation(self):
        for bad in ([], [{"participant_id":"X","weight":1}],
                    [{"participant_id":"A","weight":1},{"participant_id":"A","weight":2}],
                    [{"participant_id":"A","weight":True}]):
            b = example()
            b["items"][0]["claims"] = bad
            with self.assertRaises(BillError):
                solve(b)

    def test_actual_portions(self):
        b = example()
        b["items"][-1]["portions"] = 4
        with self.assertRaises(BillError):
            solve(b)

    def test_duplicate_charge_ids(self):
        b = example()
        b["items"][1]["id"] = b["items"][0]["id"]
        with self.assertRaises(BillError):
            solve(b)

    def test_participant_display_order_does_not_change_totals(self):
        b = example()
        expected = {p["id"]:p["total_minor"] for p in solve(b)["people"]}
        b["participants"].reverse()
        actual = {p["id"]:p["total_minor"] for p in solve(b)["people"]}
        self.assertEqual(actual, expected)

    def test_private_extra_fields_not_exported(self):
        b = example()
        b.update(mobilePhone="PRIVATE_PHONE", deliveryInfo="PRIVATE_ADDRESS", token="PRIVATE_TOKEN")
        r = solve(b)
        for text in (json.dumps(r), render.markdown(r), render.html(r)):
            self.assertNotIn("PRIVATE_", text)

    def test_escaped_html(self):
        b = example()
        b["participants"][0]["name"] = '<script>alert("x")</script>'
        b["items"][0]["name"] = '<img src=x onerror=alert(1)>'
        html = render.html(solve(b))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("default-src 'none'", html)


class StorageTests(unittest.TestCase):
    def test_cas_and_immutable_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/"ledger.db"
            b = example()
            save_version(db, b, 0)
            with self.assertRaises(BillError):
                save_version(db, b, 0)
            b["version"] = 2
            b["discounts"][0]["policy"] = "equal"
            save_version(db, b, 1)
            rows = history(db, b["bill_id"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["result"]["people"][0]["total_minor"], 3174)
            self.assertEqual(rows[1]["result"]["people"][0]["total_minor"], 3333)
            self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_cli_output_and_input_protection(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            src, out = Path(tmp)/"bill.json", Path(tmp)/"bill.html"
            src.write_text(json.dumps(example()))
            self.assertEqual(main(["solve",str(src),"--html",str(out),"--compare","equal"]), 0)
            self.assertTrue(out.exists())
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertEqual(main(["solve",str(src),"--html",str(src)]), 2)
            self.assertEqual(json.loads(src.read_text())["paid_total_minor"], 8800)


class AdapterTests(unittest.TestCase):
    def snapshot(self):
        return safe_order({"orderStatus":"6", "realTotalAmount":"30.00", "realDeliveryPrice":"0.00",
                           "realPackingFeeTotalPrice":"0.00", "tablewarePrice":"0.00",
                           "mobilePhone":"SECRET_PHONE", "pickupCode":"SECRET_PICKUP",
                           "deliveryInfo":{"address":"SECRET_ADDRESS"},
                           "orderProductList":[{"productName":"套餐","quantity":1,"price":"45.00"}]})

    def test_snapshot_whitelist(self):
        s = self.snapshot()
        self.assertNotIn("SECRET", json.dumps(s))
        self.assertEqual(s["unit_status"], "requires_confirmation")

    def test_draft_cannot_silently_settle(self):
        b = draft(self.snapshot(), "yuan", "private-lunch")
        self.assertEqual(b["paid_total_minor"], 3000)
        self.assertIsNone(b["items"][0]["amount_minor"])
        with self.assertRaises(BillError):
            solve(b)
        b["participants"] = [{"id":"A","name":"甲"},{"id":"B","name":"乙"}]
        b["items"][0].update(amount_minor=3000, claims=[{"participant_id":"A","weight":2},{"participant_id":"B","weight":1}])
        b["unresolved"] = []
        b["rules_confirmed"] = True
        self.assertEqual([r["total_minor"] for r in solve(b)["people"]], [2000,1000])

    def test_missing_fee_is_unknown(self):
        s = self.snapshot()
        del s["order"]["tablewarePrice"]
        b = draft(s, "yuan", "private-lunch")
        self.assertIsNone(b["fees"][-1]["amount_minor"])
        self.assertTrue(any("餐具费" in s for s in b["unresolved"]))

    def test_cancelled_not_paid(self):
        s = self.snapshot()
        s["order"]["orderStatus"] = "7"
        self.assertEqual(draft(s,"yuan","cancelled")["payment_status"], "unknown")

    def test_mutation_blocked_before_network(self):
        with patch.dict("os.environ", {"MCD_MCP_TOKEN":"fixture-only"}):
            client = Client()
            for tool in ("create-order", "cancel-order", "mall-create-order", "auto-bind-coupons"):
                with self.assertRaises(MCPError):
                    client.call(tool,{})

    def test_missing_token(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(MCPError):
                Client()

    def test_mcp_json_and_sse(self):
        class Response:
            headers = {}
            def __init__(self, raw): self.raw = raw
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,*args): return self.raw
        class Opener:
            def __init__(self, raw): self.raw = raw
            def open(self,*args,**kwargs): return Response(self.raw)
        with patch.dict("os.environ", {"MCD_MCP_TOKEN":"fixture-only"}):
            for raw in (b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
                        b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'):
                client = Client()
                client.opener = Opener(raw)
                self.assertEqual(client.rpc("initialize"), {"ok":True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
