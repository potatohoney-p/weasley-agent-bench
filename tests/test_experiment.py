import unittest

from experiments.fault_matrix import run_experiment


class ExperimentTests(unittest.TestCase):
    def test_fault_matrix_and_valid_control_match_declared_invariants(self):
        report = run_experiment()
        self.assertEqual(report["summary"], {
            "faults": 12, "controls": 1, "full_contract_detected": 12,
            "ablation_detected": 5, "full_contract_matches_expectations": True,
        })
        control = report["results"][0]
        self.assertFalse(control["full_contract"]["rejected"])
        self.assertFalse(control["final_state_ablation"]["rejected"])
        self.assertEqual(report, run_experiment())


if __name__ == "__main__":
    unittest.main()
