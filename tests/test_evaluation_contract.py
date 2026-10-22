import unittest

from evaluation_contract import EvaluationContractError, result_digest, summarize, validate_result


def sample(status="PASS"):
    return {
        "schema_version": "1", "evaluation_id": "runtime.allow",
        "project_id": "birdeye", "requirement_id": "allow-path",
        "status": status, "source_revision": "abc123", "runner": "integration",
        "evidence": [{"kind": "runtime_receipt", "reference": "receipt-001"}],
    }


class EvaluationContractTests(unittest.TestCase):
    def test_valid_pass(self):
        self.assertEqual(validate_result(sample())["status"], "PASS")

    def test_pass_without_evidence_is_rejected(self):
        record = sample()
        record["evidence"] = []
        with self.assertRaises(EvaluationContractError):
            validate_result(record)

    def test_unknown_status_rejected(self):
        with self.assertRaises(EvaluationContractError):
            validate_result(sample("SUCCESS"))

    def test_missing_required_evaluation_is_unverified(self):
        self.assertEqual(summarize([sample()], ["runtime.allow", "runtime.deny"])["status"], "UNVERIFIED")

    def test_duplicate_results_cannot_pass(self):
        self.assertEqual(summarize([sample(), sample()], ["runtime.allow"])["status"], "FAIL")

    def test_failed_result_dominates(self):
        self.assertEqual(summarize([sample("FAIL")], ["runtime.allow"])["status"], "FAIL")

    def test_digest_is_deterministic(self):
        self.assertEqual(result_digest(sample()), result_digest(dict(reversed(list(sample().items())))))


if __name__ == "__main__":
    unittest.main()
