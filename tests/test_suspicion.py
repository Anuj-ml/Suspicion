"""One runnable check for the non-trivial logic. Stdlib only."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SUSPICION_LLM_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
# Keep the test run out of the real verdict store.
os.environ.setdefault(
    "SUSPICION_DB_PATH", os.path.join(tempfile.gettempdir(), "suspicion_test.db")
)
os.environ.pop("SUSPICION_PUBLIC_URL", None)
os.environ.pop("RENDER_EXTERNAL_URL", None)

from app.checks import evaluate, fee_signal
from app.extract import extract
from app.redact import evidence_quote, redact
from app.scoring import band

SCAM = (
    "Company: Acme Careers\n"
    "We are hiring freshers. Pay a registration fee of ₹2500 via UPI to confirm.\n"
    "Contact hr.acme.careers@gmail.com or WhatsApp 9876543210. "
    "Submit Aadhaar and PAN through this Google Form. Join within 24 hours."
)
NIP = (
    "This internship is listed on the National Internship Portal "
    "(internship.aicte-india.org). Pay a training fee of ₹1000 via UPI."
)
LEGIT = "Company: Acme. Interview scheduled. Stipend ₹15000. No fee at any stage."


class RedactionTest(unittest.TestCase):
    def test_phone_removed_domain_kept(self):
        out = redact(SCAM)
        self.assertNotIn("9876543210", out)
        self.assertIn("gmail.com", out)
        self.assertIn("[PHONE]", out)

    def test_evidence_never_leaks_phone(self):
        self.assertNotIn("9876543210", evidence_quote(SCAM))


class CheckTest(unittest.TestCase):
    def test_fee_is_hard_only_with_nip_provenance(self):
        soft = fee_signal(SCAM, extract(SCAM))
        hard = fee_signal(NIP, extract(NIP))
        self.assertEqual(soft.severity, "heuristic")
        self.assertEqual(hard.severity, "hard")

    def test_band_cost_asymmetry(self):
        signals = evaluate(SCAM, extract(SCAM), online=False, country="IN")
        self.assertEqual(band(signals), "High")
        self.assertEqual(band(evaluate(LEGIT, extract(LEGIT))), "Low")
        self.assertEqual(band(evaluate(NIP, extract(NIP))), "High")

    def test_terms_are_info_not_risk(self):
        text = "Company: Acme. Unpaid training for 3 months, then a bond."
        signals = evaluate(text, extract(text))
        terms = [s for s in signals if s.code == "terms_review"]
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].severity, "info")
        self.assertEqual(band(signals), "Low")

    def test_mlm_needs_all_three_conditions(self):
        text = (
            "Company: Acme. Pay a registration fee of ₹500. "
            "Earn per referral. No product involved. Work from home."
        )
        offer = extract(text)
        offer.has_product = False
        codes = {s.code for s in evaluate(text, offer)}
        self.assertIn("mlm_pattern", codes)


class PresentationTest(unittest.TestCase):
    def test_one_finding_per_issue_not_per_regex(self):
        from app.checks import findings

        offer = extract(SCAM)
        signals = evaluate(SCAM, offer, online=False)
        items = findings(signals, offer)
        money = [i for i in items if i["category"] == "a"]
        self.assertEqual(len(money), 1, "fee + UPI must collapse into one finding")
        for item in items:
            self.assertTrue(item["headline"])
            self.assertTrue(item["action"])

    def test_checks_are_deduped(self):
        from app.checks import checks, evaluate

        offer = extract(SCAM)
        rows = checks(evaluate(SCAM, offer, online=False))
        labels = [row["label"] for row in rows]
        self.assertEqual(len(labels), len(set(labels)))

    def test_html_escapes_and_keeps_formatting(self):
        from app.main import format_result, run_check

        result = run_check("Pay ₹500 <b>now</b> & send Aadhaar", online=False)
        html = format_result(result)
        self.assertIn("<b>", html)
        self.assertNotIn("<b>now</b>", html)
        self.assertIn("&amp;", html)


class LookupFilterTest(unittest.TestCase):
    """A fraud word near a name is not an accusation of that name."""

    def test_denial_and_distance_do_not_count(self):
        from app.lookup import _accuses

        self.assertTrue(_accuses("Major Job Scam in Noida Using Amazon Name", "Amazon"))
        self.assertTrue(_accuses("Acme Careers job scam warning", "Acme Careers"))
        self.assertFalse(_accuses("Zoho CEO denies allegations of fraud", "Zoho"))
        self.assertFalse(_accuses("Zoho founder reacts to AI voice scam", "Zoho"))

    def test_upi_suffix_is_not_a_sender_domain(self):
        from app.extract import Offer, _merge

        merged = _merge(Offer(), {"sender_domain": "okaxis"})
        self.assertEqual(merged.sender_domain, "")


class OffsetTest(unittest.TestCase):
    """A redeploy must not re-answer messages that were already handled."""

    def test_fresh_database_has_no_offset(self):
        from app import store

        store.init_db()
        store.set_offset(0)  # table exists, value not meaningful
        with store._db() as conn:
            conn.execute("DELETE FROM meta")
        self.assertIsNone(store.get_offset())

    def test_offset_survives_writes(self):
        from app import store

        store.init_db()
        store.set_offset(41)
        store.set_offset(42)
        self.assertEqual(store.get_offset(), 42)

    def test_update_date_reads_message_and_callback(self):
        from app.telegram import _update_date

        self.assertEqual(_update_date({"message": {"date": 1700000000}}), 1700000000)
        self.assertEqual(_update_date({"edited_message": {"date": 5}}), 5)
        self.assertEqual(
            _update_date({"callback_query": {"message": {"date": 7}}}), 7
        )
        self.assertEqual(_update_date({"poll": {}}), 0)


if __name__ == "__main__":
    unittest.main()
