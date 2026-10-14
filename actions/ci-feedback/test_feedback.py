"""Exercise delivery failures without network calls or publishing test issues."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import ssl
import unittest
import unittest.mock as mock
import urllib.error


spec = importlib.util.spec_from_file_location("feedback", Path(__file__).with_name("feedback.py"))
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)
PRIVATE = "private-response-token-and-url"


class FeedbackDiagnosticsTests(unittest.TestCase):
    def invoke(self, operation):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (mock.patch.object(feedback, "main", side_effect=operation),
              contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
            code = feedback.cli()
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(PRIVATE, stderr.getvalue())
        result = json.loads(stderr.getvalue())
        self.assertEqual(result.pop("status"), "failed")
        return result

    def raised(self, error):
        def operation():
            raise error
        return self.invoke(operation)

    def api(self):
        api = feedback.GitHubAPI("example/project", PRIVATE)
        api.opener = mock.Mock()
        return api

    def test_http_status_survives_without_response_text_or_retry(self):
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                api = self.api()
                response = io.BytesIO(PRIVATE.encode())
                api.opener.open.side_effect = urllib.error.HTTPError(
                    "https://api.github.com/" + PRIVATE, status, PRIVATE,
                    {"Retry-After": PRIVATE}, response)
                result = self.invoke(lambda: api.request("/repos/example/project/issues"))
                self.assertEqual(result, {"error_code": "api_http", "http_status": status})
                self.assertEqual(response.tell(), 0)
                self.assertEqual(api.opener.open.call_count, 1)

    def test_transport_causes_use_exception_types_without_reason_text(self):
        for error, code in (
            (ssl.SSLCertVerificationError(PRIVATE), "transport_tls"),
            (TimeoutError(PRIVATE), "transport_timeout"),
            (socket.gaierror(PRIVATE), "transport_dns"),
            (PRIVATE, "transport_failure"),
        ):
            with self.subTest(code=code):
                api = self.api()
                api.opener.open.side_effect = urllib.error.URLError(error)
                result = self.invoke(lambda: api.request("/repos/example/project/issues"))
                self.assertEqual(result, {"error_code": code})
                self.assertEqual(api.opener.open.call_count, 1)
        self.assertEqual(self.raised(TimeoutError(PRIVATE)), {"error_code": "transport_timeout"})

    def test_redirect_is_refused_without_exposing_destination(self):
        result = self.invoke(lambda: feedback.NoRedirect().redirect_request(
            None, None, 302, PRIVATE, {}, "https://" + PRIVATE))
        self.assertEqual(result, {"error_code": "api_redirect"})

    def test_invalid_and_oversized_response_remain_bounded(self):
        for raw, code in (
            (PRIVATE.encode(), "api_response_json"),
            (b'"\xff"', "api_response_json"),
            (b"x" * (feedback.MAX_RESPONSE + 1), "api_response_size"),
        ):
            with self.subTest(code=code):
                api = self.api()
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = raw
                api.opener.open.return_value = response
                result = self.invoke(lambda: api.request("/repos/example/project/issues"))
                self.assertEqual(result, {"error_code": code})
                response.read.assert_called_once_with(feedback.MAX_RESPONSE + 1)

    def test_foreign_run_is_refused_before_jobs_or_issue_writes(self):
        api = mock.Mock()
        api.request.return_value = {
            "id": 7, "run_attempt": 1,
            "repository": {"id": 99, "full_name": "example/project"},
        }
        result = self.invoke(lambda: feedback.publish(api, "example/project", 42, 7, 1))
        self.assertEqual(result, {"error_code": "run_identity"})
        api.request.assert_called_once_with("/repos/example/project/actions/runs/7/attempts/1")

    def test_arbitrary_errors_and_missing_configuration_stay_private(self):
        for error, code in (
            (KeyError(PRIVATE), "required_field_missing"),
            (ValueError(PRIVATE), "invalid_input"),
            (OSError(PRIVATE), "io_failure"),
            (type(PRIVATE, (Exception,), {})(PRIVATE), "unknown"),
        ):
            with self.subTest(code=code):
                self.assertEqual(self.raised(error), {"error_code": code})
        with self.assertRaises(ValueError):
            feedback.FeedbackFailure(PRIVATE)
        original_main = feedback.main
        with mock.patch.dict(os.environ, {"GITHUB_SERVER_URL": "https://github.com"}, clear=True):
            self.assertEqual(self.invoke(original_main), {"error_code": "required_field_missing"})

    def test_ambiguous_issue_creation_retains_existing_deduplication(self):
        api = mock.Mock()
        api.request.side_effect = [
            {"id": 7, "run_attempt": 1, "status": "completed", "conclusion": "failure",
             "head_sha": "a" * 40, "created_at": "2026-10-14T00:00:00Z", "workflow_id": 8,
             "repository": {"id": 42, "full_name": "example/project"}},
            feedback.FeedbackFailure("api_http", http_status=502),
        ]
        existing = {"status": "already-published", "issue_number": 9}
        with (mock.patch.object(feedback, "read_jobs", return_value=[
                {"id": 10, "status": "completed", "conclusion": "failure"}]),
              mock.patch.object(feedback, "find_published", side_effect=[None, existing])):
            self.assertEqual(feedback.publish(api, "example/project", 42, 7, 1), existing)
        self.assertEqual(len(api.request.call_args_list), 2)
        self.assertEqual(api.request.call_args_list[1].args[0], "/repos/example/project/issues")


if __name__ == "__main__":
    unittest.main()
