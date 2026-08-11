#!/usr/bin/env python3
"""Offline tests — no credentials, no network. Run: python3 tests/test_gads_scan.py"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gads_scan as g  # noqa: E402


class Enumish:
    def __init__(self, name):
        self.name = name


class FakeRow:
    """Minimal stand-in for a GoogleAdsRow — nested attribute access only."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Bag:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class FakeClient:
    """A client whose search_stream returns canned rows per query fragment."""
    def __init__(self, by_fragment):
        self.by_fragment = by_fragment
        self.queries = []
        self.login_customer_id = "1234567890"

    def get_service(self, _name):
        return self

    def search_stream(self, customer_id, query):
        self.queries.append(query)
        for fragment, rows in self.by_fragment.items():
            if fragment in query:
                return [Bag(results=rows)]
        return [Bag(results=[])]


class TestMonthBounds(unittest.TestCase):
    def test_full_past_month(self):
        self.assertEqual(g.month_bounds("2026-07"), ("2026-07-01", "2026-07-31"))

    def test_february_leap(self):
        self.assertEqual(g.month_bounds("2024-02"), ("2024-02-01", "2024-02-29"))

    def test_current_month_clamped_to_today(self):
        today = date.today()
        start, end = g.month_bounds(f"{today.year:04d}-{today.month:02d}")
        self.assertEqual(start, f"{today.year:04d}-{today.month:02d}-01")
        self.assertEqual(end, today.isoformat())

    def test_future_month_rejected(self):
        year = date.today().year + 1
        with self.assertRaises(ValueError):
            g.month_bounds(f"{year}-01")

    def test_bad_format_rejected(self):
        for bad in ("2026-7", "july", "2026/07", "", None, "2026-13"):
            with self.assertRaises(ValueError):
                g.month_bounds(bad)


class TestDateClause(unittest.TestCase):
    def test_during_literal(self):
        self.assertEqual(g.date_clause("LAST_30_DAYS"), "segments.date DURING LAST_30_DAYS")

    def test_lowercase_accepted(self):
        self.assertEqual(g.date_clause("this_month"), "segments.date DURING THIS_MONTH")

    def test_month_becomes_between(self):
        self.assertEqual(g.date_clause("2026-07"),
                         "segments.date BETWEEN '2026-07-01' AND '2026-07-31'")

    def test_invalid_during_rejected_before_the_api_call(self):
        # LAST_90_DAYS and LAST_29_DAYS look plausible; Google rejects both.
        for bad in ("LAST_90_DAYS", "LAST_29_DAYS", "LAST_QUARTER"):
            with self.assertRaises(ValueError):
                g.date_clause(bad)


class TestCustomerId(unittest.TestCase):
    def test_dashes_stripped(self):
        self.assertEqual(g.normalize_customer_id("123-456-7890"), "1234567890")

    def test_plain_digits(self):
        self.assertEqual(g.normalize_customer_id("1234567890"), "1234567890")

    def test_wrong_length_rejected(self):
        for bad in ("123", "12345678901", "abc", ""):
            with self.assertRaises(ValueError):
                g.normalize_customer_id(bad)


class TestTable(unittest.TestCase):
    def test_header_and_rows(self):
        out = g.table(["A", "B"], [[1, 2], [3, 4]])
        self.assertEqual(out[0], "| A | B |")
        self.assertEqual(out[1], "|---|---|")
        self.assertEqual(out[-1], "| 3 | 4 |")

    def test_empty_uses_placeholder(self):
        self.assertEqual(g.table(["A"], [], "_(none)_"), ["_(none)_"])


class TestTransientDetection(unittest.TestCase):
    def test_http_503_is_transient(self):
        exc = Exception("boom")
        exc.code = 503
        self.assertTrue(g.is_transient(exc))

    def test_named_exception_is_transient(self):
        self.assertTrue(g.is_transient(type("ServiceUnavailable", (Exception,), {})()))

    def test_plain_error_is_not_transient(self):
        self.assertFalse(g.is_transient(ValueError("bad GAQL")))


class TestSectionIsolation(unittest.TestCase):
    def test_failing_section_degrades_alone(self):
        def boom(client, cid, spec):
            raise RuntimeError("query exploded")
        out = g.run_section(boom, None, "1", "LAST_30_DAYS")
        self.assertIn("DEGRADED", out[0])
        self.assertIn("query exploded", out[1])


class TestConversionSection(unittest.TestCase):
    def _client(self, counting, category="CONTACT"):
        action = Bag(name="Phone calls", type_=Enumish("AD_CALL"),
                     category=Enumish(category), primary_for_goal=True,
                     counting_type=Enumish(counting))
        return FakeClient({
            "FROM conversion_action": [FakeRow(conversion_action=action)],
            "FROM customer": [FakeRow(segments=Bag(conversion_action_name="Phone calls"),
                                      metrics=Bag(conversions=12.0))],
        })

    def test_many_per_click_lead_is_flagged(self):
        out = "\n".join(g.section_conversions(self._client("MANY_PER_CLICK"), "1", "LAST_30_DAYS"))
        self.assertIn("(!)", out)
        self.assertIn("inflates", out)
        self.assertIn("12.0", out)

    def test_one_per_click_is_clean(self):
        out = "\n".join(g.section_conversions(self._client("ONE_PER_CLICK"), "1", "LAST_30_DAYS"))
        self.assertNotIn("(!)", out)
        self.assertIn("can be read at face value", out)

    def test_many_per_click_on_a_purchase_is_not_flagged(self):
        out = "\n".join(g.section_conversions(
            self._client("MANY_PER_CLICK", category="PURCHASE"), "1", "LAST_30_DAYS"))
        self.assertNotIn("(!)", out)


class TestWasteSection(unittest.TestCase):
    def test_totals_and_caution(self):
        rows = [FakeRow(ad_group_criterion=Bag(keyword=Bag(text="plumber cost")),
                        campaign=Bag(name="Search - Core"),
                        metrics=Bag(cost_micros=12_050_000, clicks=9))]
        out = "\n".join(g.section_waste(FakeClient({"FROM keyword_view": rows}), "1", "LAST_30_DAYS"))
        self.assertIn("plumber cost", out)
        self.assertIn("$12.05", out)
        self.assertIn("not been tested", out)

    def test_no_waste_says_so(self):
        out = "\n".join(g.section_waste(FakeClient({}), "1", "LAST_30_DAYS"))
        self.assertIn("also converted", out)


class TestScanComposition(unittest.TestCase):
    def test_single_section_only(self):
        report = g.scan("1234567890", "LAST_30_DAYS", only="waste", client=FakeClient({}))
        self.assertIn("Wasted spend", report)
        self.assertNotIn("Conversion integrity", report)

    def test_full_scan_lists_every_section(self):
        report = g.scan("1234567890", "LAST_30_DAYS", client=FakeClient({}))
        for title in ("Conversion integrity", "Bidding strategy", "Device",
                      "Day of week", "Hour of day", "Wasted spend",
                      "Converting search terms", "Assets", "Month over month"):
            self.assertIn(title, report)

    def test_month_range_reaches_the_query(self):
        client = FakeClient({})
        g.scan("1234567890", "2026-07", only="waste", client=client)
        self.assertTrue(any("BETWEEN '2026-07-01' AND '2026-07-31'" in q for q in client.queries))


class TestCli(unittest.TestCase):
    def test_requires_a_target(self):
        with self.assertRaises(SystemExit):
            g.main([])

    def test_invalid_month_exits_before_auth(self):
        with self.assertRaises(SystemExit):
            g.main(["--customer-id", "123-456-7890", "--month", "2026-13"])

    def test_days_choice_is_constrained(self):
        with self.assertRaises(SystemExit):
            g.main(["--customer-id", "123-456-7890", "--days", "29"])

    def test_no_mutation_verbs_in_the_source(self):
        src = (Path(__file__).resolve().parents[1] / "gads_scan.py").read_text()
        for forbidden in ("mutate", "MutateOperation", "CampaignOperation",
                          "update_budget", "pause_campaign"):
            self.assertNotIn(forbidden, src, f"read-only tool must not contain {forbidden}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
