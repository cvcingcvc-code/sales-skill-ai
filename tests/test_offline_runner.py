"""Exercise runner failure modes with temporary tests, without recursive discovery."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest
from argparse import Namespace
from unittest.mock import patch

from scripts import run_tests


class OfflineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.report_path = self.root / "report.json"
        self.sequence = 0

    def test_focused_groups_expand_to_known_test_modules_without_duplicates(self):
        patterns = run_tests._patterns(Namespace(group=["support", "models"], pattern="test_*.py"))

        self.assertIn("test_customer_service.py", patterns)
        self.assertIn("test_model_gateway.py", patterns)
        self.assertEqual(len(patterns), len(set(patterns)))
        self.assertEqual(run_tests._patterns(Namespace(group=None, pattern="test_selected_*.py")),
                         ("test_selected_*.py",))

    def test_group_rejects_a_custom_pattern(self):
        with self.assertRaises(ValueError):
            run_tests._patterns(Namespace(group=["support"], pattern="test_selected_*.py"))

    def run_fixture(self, body="", *, arguments=(), report_path=None, write_report=True):
        self.sequence += 1
        source = self.root / f"fixture_{self.sequence}.py"
        source.write_text("import socket\nimport unittest\n" + textwrap.dedent(body), encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"offline_fixture_{self.sequence}", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite = unittest.TestLoader().loadTestsFromModule(module)
        output, errors = io.StringIO(), io.StringIO()
        path = self.report_path if report_path is None else report_path
        argv = ["--quiet", *arguments]
        if write_report:
            argv.extend(["--report-json", str(path)])
        with patch.object(run_tests.unittest.defaultTestLoader, "discover", return_value=suite) as discover, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = run_tests.main(argv)
        report = json.loads(path.read_text(encoding="utf-8")) if write_report and path.is_file() else None
        return code, report, output.getvalue(), errors.getvalue(), discover.call_args

    def test_zero_discovered_tests_fail_instead_of_false_pass(self):
        code, report, _, errors, _ = self.run_fixture()
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["run"]), ("FAIL", 0))
        self.assertEqual((report["fail"], report["error"], report["network_attempts"]), (0, 0, 0))
        self.assertIn("no tests ran", errors)

    def test_success_preserves_pattern_and_quiet_arguments(self):
        code, report, output, _, discovery = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertEqual(2 + 2, 4)
        ''', arguments=("--pattern", "test_selected_*.py"))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((report["run"], report["skipped"], report["network_attempts"]), (1, 0, 0))
        self.assertEqual(discovery.kwargs["pattern"], "test_selected_*.py")
        self.assertEqual(json.loads(output.splitlines()[-1]), report)

    def test_existing_invocation_without_report_remains_supported(self):
        code, report, output, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertTrue(True)
        ''', write_report=False)
        self.assertEqual(code, 0)
        self.assertIsNone(report)
        self.assertFalse(self.report_path.exists())
        self.assertIn("Offline network guard: 0 connection attempts", output)

    def test_skips_remain_optional_for_existing_local_usage(self):
        code, report, _, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                @unittest.skip("fixture optional dependency")
                def test_skip(self):
                    self.fail("must not run")
        ''')
        self.assertEqual(code, 0)
        self.assertEqual((report["status"], report["run"], report["skipped"]), ("PASS", 1, 1))
        self.assertFalse(report["fail_on_skip"])

    def test_strict_mode_rejects_skipped_test_even_with_other_success(self):
        code, report, _, errors, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertTrue(True)
                @unittest.skip("fixture optional dependency")
                def test_skip(self):
                    self.fail("must not run")
        ''', arguments=("--fail-on-skip",))
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["run"], report["skipped"]), ("FAIL", 2, 1))
        self.assertTrue(report["fail_on_skip"])
        self.assertIn("skipped tests are not allowed", errors)

    def test_failure_and_error_counts_are_written_without_exception_details(self):
        code, report, _, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_private_failure(self):
                    self.fail("fixture-customer-contact@example.invalid")
                def test_private_error(self):
                    raise RuntimeError("fixture-private-credential")
        ''')
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["run"], report["fail"], report["error"]),
                         ("FAIL", 2, 1, 1))
        self.assertEqual(report["scope"], "offline_mock_regression")
        self.assertEqual(report["model_validation"], "NOT_RUN")
        self.assertEqual(set(report), {"scope", "model_validation", "status", "strict", "fail_on_skip", "run", "fail",
                                      "error", "skipped", "expected_failures", "unexpected_successes", "network_attempts"})
        serialized = self.report_path.read_text(encoding="utf-8")
        for private in ("fixture-private-credential", "fixture-customer-contact", "test_private", str(self.root)):
            self.assertNotIn(private, serialized)

    def test_strict_rejects_expected_failure_instead_of_reporting_pass(self):
        code, report, _, errors, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                @unittest.expectedFailure
                def test_known_defect(self):
                    self.assertEqual(1, 2)
        ''', arguments=("--strict",))
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["run"], report["expected_failures"]), ("FAIL", 1, 1))
        self.assertTrue(report["strict"])
        self.assertIn("expected failures are not allowed", errors)

    def test_expected_failure_remains_optional_outside_strict_mode(self):
        for arguments in ((), ("--fail-on-skip",)):
            with self.subTest(arguments=arguments):
                code, report, _, _, _ = self.run_fixture('''
                    class Example(unittest.TestCase):
                        @unittest.expectedFailure
                        def test_known_defect(self):
                            self.assertEqual(1, 2)
                ''', arguments=arguments)
                self.assertEqual((code, report["status"], report["expected_failures"]), (0, "PASS", 1))
                self.assertFalse(report["strict"])

    def test_strict_rejects_skips_but_accepts_completed_success(self):
        skipped = self.run_fixture('''
            class Example(unittest.TestCase):
                @unittest.skip("fixture only")
                def test_skip(self):
                    pass
        ''', arguments=("--strict",))
        self.assertEqual((skipped[0], skipped[1]["status"], skipped[1]["skipped"]), (1, "FAIL", 1))
        completed = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_completed(self):
                    self.assertTrue(True)
        ''', arguments=("--strict",))
        self.assertEqual((completed[0], completed[1]["status"], completed[1]["run"]), (0, "PASS", 1))
        self.assertTrue(completed[1]["strict"])
        self.assertTrue(completed[1]["fail_on_skip"])

    def test_caught_network_attempt_still_fails_without_connecting(self):
        code, report, _, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_caught_connection(self):
                    with self.assertRaises(RuntimeError):
                        socket.create_connection(("fixture.invalid", 443))
        ''')
        self.assertEqual(code, 1)
        self.assertEqual((report["status"], report["run"], report["network_attempts"]), ("FAIL", 1, 1))
        self.assertEqual((report["fail"], report["error"]), (0, 0))

    def test_report_write_failure_returns_failure_without_echoing_destination(self):
        code, report, output, errors, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertTrue(True)
        ''', report_path=self.root)
        self.assertEqual(code, 1)
        self.assertIsNone(report)
        self.assertIn("JSON report could not be written", errors)
        self.assertNotIn(str(self.root), errors)
        self.assertEqual(json.loads(output.splitlines()[-1])["status"], "FAIL")
        self.assertEqual(list(self.root.glob(".offline-report-*.tmp")), [])

    def test_requested_report_creates_missing_parent_directory(self):
        path = self.root / "missing" / "nested" / "summary.json"
        code, report, _, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertTrue(True)
        ''', report_path=path)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(path.is_file())

    def test_requested_report_replaces_stale_result_without_temporary_files(self):
        path = self.root / "nested" / "summary.json"
        path.parent.mkdir()
        path.write_text('{"status": "STALE"}', encoding="utf-8")
        code, report, _, _, _ = self.run_fixture('''
            class Example(unittest.TestCase):
                def test_pass(self):
                    self.assertTrue(True)
        ''', report_path=path)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(list(path.parent.glob(".offline-report-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
