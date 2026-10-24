"""分账规则、文件保存和MCP响应处理的回归测试。"""
import contextlib
import copy
import io
import json
from pathlib import Path
from http.client import IncompleteRead
import random
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fairsplit import BillError, allocate, compare, history, money, save_version, solve, yuan
from cli import main, draft, write, ROOT
from json_input import JSONInputError, parse_json
from mcp_readonly import Client, MCPError, read_result, safe_order, safe_orders
import render


def example(which="four_people"):
    return json.loads((ROOT/"assets"/"examples"/(which+".json")).read_text())


class MoneyTests(unittest.TestCase):
    def test_leading_zero_amounts_keep_their_value(self):
        self.assertEqual(yuan("000000000012.34"), 1234)
        self.assertEqual(yuan("0"*5000 + ".01"), 1)
        self.assertEqual(yuan("0"*5000), 0)
        with self.assertRaises(BillError):
            yuan("0"*5000 + "10000000.01")

    def test_decimal_conversion(self):
        self.assertEqual(yuan("0.29"), 29)
        self.assertEqual(yuan("12.3"), 1230)
        self.assertEqual(money(-1), "−0.01")
        for bad in (0.29, "0.291", "NaN", "1e3", "-1", "0.01\n", True):
            with self.assertRaises(BillError):
                yuan(bad)

    def test_yuan_is_exact_under_narrow_decimal_context(self):
        import decimal
        self.assertEqual(yuan("10.00"), 1000)
        with decimal.localcontext() as context:
            context.prec = 2
            self.assertEqual(yuan("12.34"), 1234)
            self.assertEqual(yuan("0.99"), 99)

    def test_overlong_yuan_digits_raise_bill_error(self):
        for bad in ("9"*5000, "9"*11 + ".5", "99999999.99"):
            with self.assertRaises(BillError):
                yuan(bad)
        self.assertEqual(yuan("9999999.99"), 999999999)

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
    def test_invalid_field_types_are_bill_errors(self):
        for field in ("data_mode", "payer_id"):
            b = example()
            b[field] = []
            with self.subTest(field=field), self.assertRaises(BillError):
                solve(b)
        for obj, field, value in (("item", "source", {}), ("claim", "participant_id", []),
                                  ("discount", "source", []), ("discount", "eligible", [{}]),
                                  ("discount", "eligible_items", [[]]), ("discount", "policy", {})):
            b = example()
            target = b["items"][0] if obj == "item" else (b["items"][0]["claims"][0] if obj == "claim" else b["discounts"][0])
            target[field] = value
            with self.subTest(field=field), self.assertRaises(BillError):
                solve(b)

    def test_invalid_unicode_and_padded_names_rejected(self):
        for bad in ("\ud800", "\udfff", " "*120 + "甲", " "):
            b = example()
            b["participants"][0]["name"] = bad
            with self.assertRaises(BillError):
                solve(b)
        b = example()
        b["participants"][0]["name"] = "甲😀"
        render.markdown(solve(b)).encode("utf-8")

    def test_exports_explain_exact_discount_product_scope(self):
        b = example()
        b["items"][1]["name"] = "限定餐品X"
        b["discounts"][0]["eligible_items"] = [b["items"][1]["id"]]
        r = solve(b)
        coupon_line = next(line for line in render.markdown(r).splitlines() if line.startswith("- " + b["discounts"][0]["name"]))
        self.assertIn("适用商品：限定餐品X", coupon_line)
        self.assertNotIn(b["items"][-1]["name"], coupon_line)
        rules = render.html(r).split('<ul class="rules">')[1].split("</ul>")[0]
        self.assertIn("适用商品：限定餐品X", rules)
        self.assertNotIn(b["items"][-1]["name"], rules)

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

    def test_fee_cannot_silently_ignore_product_scope(self):
        b = example()
        b["fees"][0]["eligible_items"] = ["drink-C"]
        with self.assertRaisesRegex(BillError, "eligible_items"):
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

    def test_zero_discount_and_literal_names(self):
        b = example("bundle")
        b["participants"][0]["name"] = "小周|[付款](https://example.invalid) $style"
        b["items"][0]["name"] = "**套餐** <script>"
        result = solve(b)
        html = render.html(result)
        md = render.markdown(result)
        self.assertNotIn("−0.00", html)
        self.assertNotIn("−0.00", md)
        self.assertIn("$style", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn(r"小周\|\[付款\]\(https://example\.invalid\)", md)
        self.assertIn(r"\*\*套餐\*\*", md)


class StorageTests(unittest.TestCase):
    def test_invalid_json_never_replaces_existing_export(self):
        bad_inputs = ('{"paid_total_minor":100,"paid_total_minor":200}',
                      '{"items":[{"amount_minor":100,"amount_minor":200}]}',
                      '{"rules_confirmed":true,"rules_confirmed":false}',
                      '{"amount_minor":NaN}', '['*1100 + '0' + ']'*1100)
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/"bill.json", Path(tmp)/"bill.html"
            for raw in bad_inputs:
                src.write_text(raw)
                out.write_text("previous bill")
                error = io.StringIO()
                with contextlib.redirect_stderr(error), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["solve",str(src),"--html",str(out)]), 2)
                self.assertNotIn("Traceback", error.getvalue())
                self.assertEqual(out.read_text(), "previous bill")

    def test_lone_surrogate_in_input_is_controlled_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/"bill.json", Path(tmp)/"bill.html"
            b = example()
            b["items"][0]["name"] = "\ud800"
            src.write_text(json.dumps(b))
            error = io.StringIO()
            with contextlib.redirect_stderr(error), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["solve",str(src),"--html",str(out)]), 2)
            self.assertNotIn("Traceback", error.getvalue())
            self.assertFalse(out.exists())

    def test_saved_bill_excludes_unused_private_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/"ledger.db"
            b = example()
            b["token"] = "DO_NOT_STORE"
            b["snapshot_digest"] = {"token":"DO_NOT_STORE"}
            b["participants"][0]["mobilePhone"] = "DO_NOT_STORE"
            b["items"][0]["original_response"] = {"secret":"DO_NOT_STORE"}
            b["fees"][0]["deliveryInfo"] = {"secret":"DO_NOT_STORE"}
            b["fees"][0]["allocations"] = {"secret":"DO_NOT_STORE"}
            save_version(db, b, 0)
            with sqlite3.connect(db) as con:
                raw = con.execute("SELECT bill_json FROM versions").fetchone()[0]
            self.assertNotIn("DO_NOT_STORE", raw)
            self.assertEqual(solve(json.loads(raw)), solve(b))

    def test_failed_export_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/"bill.html"
            out.write_text("previous bill")
            with patch("cli.os.replace", side_effect=OSError("fixture failure")):
                with self.assertRaises(OSError):
                    write(out, "new bill")
            self.assertEqual(out.read_text(), "previous bill")
            self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_export_replaces_existing_public_file_with_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/"bill.md"
            out.write_text("old")
            out.chmod(0o644)
            write(out, "new")
            self.assertEqual(out.read_text(), "new")
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_export_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp)/"source"
            original.write_text("keep")
            link = Path(tmp)/"link"
            link.symlink_to(original)
            with self.assertRaises(BillError):
                write(link, "replace")
            self.assertEqual(original.read_text(), "keep")

    def test_cli_rejects_hardlink_to_input(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            src, out = Path(tmp)/"bill.json", Path(tmp)/"other-name.json"
            src.write_text(json.dumps(example()))
            os.link(src, out)
            self.assertEqual(main(["solve",str(src),"--json",str(out)]), 2)
            self.assertEqual(json.loads(src.read_text())["paid_total_minor"], 8800)

    def test_draft_cannot_overwrite_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            src = Path(tmp)/"snapshot.json"
            src.write_text(json.dumps(AdapterTests().snapshot()))
            self.assertEqual(main(["draft",str(src),"--total-unit","yuan","--bill-id","bill","--out",str(src)]), 2)
            self.assertEqual(json.loads(src.read_text())["source"], "official_order_snapshot")

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
    def test_leading_zero_fen_does_not_hit_integer_parse_limit(self):
        s = self.snapshot()
        s["order"]["realTotalAmount"] = "0"*5000 + "3000"
        self.assertEqual(draft(s, "fen", "bill")["paid_total_minor"], 3000)
        s["order"]["realTotalAmount"] = "0"*5000 + "1000000001"
        with self.assertRaises(BillError):
            draft(s, "fen", "bill")

    def test_json_response_can_start_with_whitespace(self):
        body = b'\n \r\n\t {"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        self.assertEqual(read_result(io.BytesIO(body), 1), {"ok":True})
        with patch("mcp_readonly.MAX_RESPONSE_BYTES", len(body)):
            self.assertEqual(read_result(io.BytesIO(body), 1), {"ok":True})
        with patch("mcp_readonly.MAX_RESPONSE_BYTES", len(body)-1):
            with self.assertRaises(MCPError):
                read_result(io.BytesIO(body), 1)

    def test_incomplete_http_read_does_not_expose_response(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def readline(self,*args): return b'{'
            def read(self,*args): raise IncompleteRead(b"PRIVATE_RESPONSE")
        with patch.dict("os.environ", {"MCD_MCP_TOKEN":"fixture-only"}):
            client = Client()
            with patch.object(client.opener, "open", return_value=Response()):
                with self.assertRaises(MCPError) as caught:
                    client.rpc("initialize")
            self.assertNotIn("PRIVATE_RESPONSE", str(caught.exception))

    def test_ambiguous_mcp_json_is_rejected(self):
        for raw in ('{"id":1,"result":{"data":{"amount":1,"amount":2}}}',
                    '{"data":Infinity}', b'\xff', '['*1100 + '0' + ']'*1100):
            with self.assertRaises(JSONInputError):
                parse_json(raw)

    def test_json_depth_limit_is_explicit(self):
        parse_json('['*64 + '0' + ']'*64)
        with self.assertRaises(JSONInputError):
            parse_json('['*65 + '0' + ']'*65)
        self.assertEqual(parse_json(json.dumps({"name":'[]{}"'*100}))["name"], '[]{}"'*100)

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
        b = draft(self.snapshot(), "yuan", "private-lunch", delivery_unit="yuan")
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
        b = draft(s, "yuan", "private-lunch", delivery_unit="yuan")
        self.assertIsNone(b["fees"][-1]["amount_minor"])
        self.assertTrue(any("餐具费" in s for s in b["unresolved"]))

    def test_delivery_unit_is_not_inferred_from_total(self):
        s = self.snapshot()
        s["order"]["realDeliveryPrice"] = "500"
        unresolved = draft(s, "yuan", "bill")
        self.assertIsNone(unresolved["fees"][0]["amount_minor"])
        explicit = draft(s, "yuan", "bill", delivery_unit="fen")
        self.assertEqual(explicit["paid_total_minor"], 3000)
        self.assertEqual(explicit["fees"][0]["amount_minor"], 500)

    def test_invalid_fen_is_reported_as_bill_error(self):
        s = self.snapshot()
        s["order"]["realTotalAmount"] = "²"
        with self.assertRaises(BillError):
            draft(s, "fen", "bill")

    def test_overlong_fen_digits_raise_bill_error_not_value_error(self):
        s = self.snapshot()
        s["order"]["realTotalAmount"] = "5"*5000
        with self.assertRaises(BillError):
            draft(s, "fen", "bill")
        s["order"]["realTotalAmount"] = "10000000000"
        with self.assertRaises(BillError):
            draft(s, "fen", "bill")
        s["order"]["realTotalAmount"] = "1000000000"
        self.assertEqual(draft(s, "fen", "bill")["paid_total_minor"], 1_000_000_000)

    def test_snapshot_rejects_nested_data_in_scalar_fields(self):
        for field in ("realTotalAmount", "storeName"):
            with self.assertRaises(MCPError):
                safe_order({field:{"secret":"PRIVATE"},"orderProductList":[]})
        with self.assertRaises(MCPError):
            safe_orders({"list":[{"orderId":{"token":"PRIVATE"}}]})

    def test_malformed_lists_do_not_become_empty_orders(self):
        for bad in ({}, {"list":None}, {"list":"not a list"}, {"list":["not an order"]}):
            with self.assertRaises(MCPError):
                safe_orders(bad)

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
            def __init__(self, raw): self.raw = io.BytesIO(raw)
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,*args): return self.raw.read(*args)
            def readline(self,*args): return self.raw.readline(*args)
        class Opener:
            def __init__(self, raw): self.raw = raw
            def open(self,*args,**kwargs): return Response(self.raw)
        with patch.dict("os.environ", {"MCD_MCP_TOKEN":"fixture-only"}):
            for raw in (b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
                        b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'):
                client = Client()
                client.opener = Opener(raw)
                self.assertEqual(client.rpc("initialize"), {"ok":True})

    def test_sse_returns_before_server_closes_connection(self):
        class OpenStream(io.BytesIO):
            def read(self, *args):
                raise AssertionError("不能等待长连接EOF")
            def readline(self, *args):
                line = super().readline(*args)
                if not line:
                    raise AssertionError("完整结果之后不能继续读")
                return line
        raw = b': keepalive\r\ndata: {"jsonrpc":"2.0","method":"progress"}\r\n\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":7,"result":{"ok":true}}\r\n\r\n'
        self.assertEqual(read_result(OpenStream(raw),7), {"ok":True})

    def test_invalid_rpc_results_fail_cleanly(self):
        for raw in (b'{"id":1,"result":[]}', b'{"id":2,"result":{}}', b'data: null\n\n', b'data: {"id":1,"error":{}}\n\n'):
            with self.assertRaises(MCPError):
                read_result(io.BytesIO(raw),1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
