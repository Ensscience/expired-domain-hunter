from __future__ import annotations

import unittest

import requests

from src.availability import AVAILABLE, PENDING, REGISTERED, UNKNOWN, VerisignRdapChecker


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.urls = []

    def get(self, url, timeout):
        self.urls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AvailabilityTests(unittest.TestCase):
    def test_authoritative_404_is_available(self):
        session = FakeSession([FakeResponse(404, {"errorCode": 404, "title": "Not Found"})])
        result = VerisignRdapChecker(session=session, max_requests=1).check("clearly-unregistered-test-name-2026.com")
        self.assertEqual(result.registration_status, AVAILABLE)
        self.assertIn("no domain object", result.reason)

    def test_domain_object_is_registered(self):
        session = FakeSession([FakeResponse(200, {"objectClassName": "domain", "status": ["ok"]})])
        result = VerisignRdapChecker(session=session, max_requests=1).check("example.com")
        self.assertEqual(result.registration_status, REGISTERED)

    def test_pending_lifecycle_is_not_hand_registerable(self):
        session = FakeSession([FakeResponse(200, {"objectClassName": "domain", "status": ["pending delete"]})])
        result = VerisignRdapChecker(session=session, max_requests=1).check("pending-example.com")
        self.assertEqual(result.registration_status, PENDING)

    def test_malformed_and_rate_limited_responses_are_unknown(self):
        malformed = VerisignRdapChecker(session=FakeSession([FakeResponse(404, ValueError("bad json"))]), max_requests=1).check("bad-response.com")
        rate_limited = VerisignRdapChecker(session=FakeSession([FakeResponse(429, {"errorCode": 429})]), max_requests=1).check("rate-limited.com")
        self.assertEqual(malformed.registration_status, UNKNOWN)
        self.assertEqual(rate_limited.registration_status, UNKNOWN)

    def test_timeout_and_5xx_are_unknown(self):
        timeout_result = VerisignRdapChecker(session=FakeSession([requests.Timeout("slow")]), max_requests=1).check("timeout-example.com")
        server_result = VerisignRdapChecker(session=FakeSession([FakeResponse(503, {"errorCode": 503})]), max_requests=1).check("server-error-example.com")
        self.assertEqual(timeout_result.registration_status, UNKNOWN)
        self.assertEqual(server_result.registration_status, UNKNOWN)

    def test_budget_exhaustion_is_unknown_and_not_available(self):
        checker = VerisignRdapChecker(session=FakeSession([]), max_requests=0)
        result = checker.check("budget-test.com")
        self.assertEqual(result.registration_status, UNKNOWN)
        self.assertIn("budget", result.reason)

    def test_invalid_domain_is_unknown(self):
        result = VerisignRdapChecker(session=FakeSession([]), max_requests=1).check("not-a-com-domain.net")
        self.assertEqual(result.registration_status, UNKNOWN)
        self.assertIn("invalid", result.reason)


if __name__ == "__main__":
    unittest.main()
