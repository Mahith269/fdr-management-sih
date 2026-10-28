import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn


class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        os.environ["FDR_DB_PATH"] = str(Path(cls.workspace.name) / "startup.db")
        os.environ["FDR_UPLOAD_DIR"] = str(Path(cls.workspace.name) / "uploads")
        os.environ["FDR_COLLEGE_PASSWORD"] = "Test-college-12345"
        os.environ["FDR_OFFICER_PASSWORD"] = "Test-officer-12345"
        import main
        cls.api = main
        ready = threading.Event()

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()

        cls.listener = socket.socket()
        cls.listener.bind(("127.0.0.1", 0))
        cls.base_url = f"http://127.0.0.1:{cls.listener.getsockname()[1]}"
        cls.server = ReadyServer(uvicorn.Config(main.app, lifespan="off", log_level="error"))
        cls.worker = threading.Thread(target=cls.server.run, kwargs={"sockets": [cls.listener]}, daemon=True)
        cls.worker.start()
        if not ready.wait(15):
            raise RuntimeError("Isolated test API did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.worker.join(10)
        cls.listener.close()
        cls.workspace.cleanup()

    def setUp(self):
        self.api.DB_PATH = Path(self.workspace.name) / f"{self.id()}.db"
        self.api.initialize_database()
        self.cookies = {}
        for role in ("college", "officer"):
            self.request("POST", "/auth/login", {"username": role, "password": f"Test-{role}-12345", "role": role}, role=role)

    def request(self, method, path, data=None, expected=200, raw=None, content_type=None, role=None, origin=None):
        headers = {}
        if role is None:
            role = "officer" if path.endswith("/review") else "college"
        if role in self.cookies and path != "/auth/login":
            headers["Cookie"] = self.cookies[role]
        if origin:
            headers["Origin"] = origin
        body = raw
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            self.response_headers = response.headers
            if path == "/auth/login" and response.status == 200:
                self.cookies[role] = response.headers["Set-Cookie"].split(";", 1)[0]
            payload = response.read()
            self.assertEqual(response.status, expected, payload[:1000])
            if payload and response.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(payload)
            return payload

    def register(self, certificate="TEST-001", verified=False):
        payload = {
            "institution_name": "Test College", "certificate_number": certificate,
            "bank_name": "Test Bank", "amount": 100000,
            "maturity_date": date.today().isoformat(),
        }
        row = self.request("POST", "/fdrs", payload, expected=201)
        if verified:
            self.request("POST", f"/fdrs/{row['id']}/submit")
            for action in ("under_review", "verified"):
                self.request("POST", f"/fdrs/{row['id']}/review", {"action": action})
        return row, payload

    def renewal(self, kind="Renewal"):
        return {
            "kind": kind, "certificate_number": "NEW-001", "bank_name": "New Bank",
            "amount": 120000, "maturity_date": (date.today() + timedelta(days=365)).isoformat(),
            "notes": "Updated certificate",
        }

    def test_login_role_permissions_and_logout(self):
        cookie_header = self.response_headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=lax", cookie_header)
        self.assertIn("Max-Age=43200", cookie_header)
        self.request("GET", "/fdrs", role="anonymous", origin="http://localhost:5173", expected=401)
        self.assertEqual(self.response_headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(self.response_headers["Access-Control-Allow-Credentials"], "true")
        self.request("GET", "/intelligence", role="anonymous", expected=401)
        for path in ("/renewal-requests", "/release-requests", "/fdrs/1/documents", "/documents/1/download"):
            self.request("GET", path, role="anonymous", expected=401)
        self.request("POST", "/assistant/query", {"action": "attention"}, role="anonymous", expected=401)
        self.request("POST", "/auth/login", {"username": "college", "password": "wrong", "role": "college"}, expected=401)
        self.request("POST", "/auth/login", {"username": "college", "password": "Test-college-12345", "role": "officer"}, expected=401)
        self.assertEqual(self.request("GET", "/auth/me", role="officer")["role"], "officer")
        row, payload = self.register()
        for path in (f"/fdrs/{row['id']}/review", "/renewal-requests/1/review", "/release-requests/1/review"):
            self.request("POST", path, {"action": "approve"}, role="college", expected=403)
        self.request("POST", "/fdrs", payload, role="officer", expected=403)
        self.request("PUT", f"/fdrs/{row['id']}", payload, role="officer", expected=403)
        self.request("POST", f"/fdrs/{row['id']}/submit", role="officer", expected=403)
        for path in ("documents", "renewal-requests", "release-requests"):
            self.request("POST", f"/fdrs/{row['id']}/{path}", role="officer", expected=403)
        self.request("POST", "/auth/logout", role="college", expected=204)
        self.request("GET", "/auth/me", role="college", expected=401)
        self.request("GET", "/fdrs", role="college", expected=401)
        self.request("POST", "/auth/logout", role="officer", origin="https://untrusted.example", expected=403)
        with sqlite3.connect(self.api.DB_PATH) as connection:
            connection.execute("UPDATE sessions SET expires_at=0")
        connection.close()
        self.request("GET", "/auth/me", role="officer", expected=401)

    def test_password_hashes_session_storage_and_login_throttling(self):
        with sqlite3.connect(self.api.DB_PATH) as connection:
            users = connection.execute("SELECT username, password_hash, password_salt FROM users").fetchall()
            tokens = [row[0] for row in connection.execute("SELECT token_hash FROM sessions")]
        connection.close()
        for username, password_hash, salt in users:
            self.assertNotEqual(password_hash, f"Test-{username}-12345")
            self.assertEqual(len(password_hash), 64)
            self.assertEqual(len(salt), 32)
        self.assertNotIn(self.cookies["college"].split("=", 1)[1], tokens)
        for _attempt in range(10):
            self.request("POST", "/auth/login", {"username": "missing", "password": "wrong", "role": "college"}, expected=401)
        self.request("POST", "/auth/login", {"username": "college", "password": "Test-college-12345", "role": "college"}, expected=429)
        with sqlite3.connect(self.api.DB_PATH) as connection:
            connection.execute("UPDATE login_attempts SET window_started=0")
        connection.close()
        self.request("POST", "/auth/login", {"username": "college", "password": "Test-college-12345", "role": "college"}, role="college")
        self.assertEqual(self.request("GET", "/auth/me")["role"], "college")

    def test_registration_correction_and_rejection(self):
        row, payload = self.register()
        self.request("POST", "/fdrs", payload, expected=409)
        self.request("POST", "/fdrs", {**payload, "certificate_number": "test-001"}, expected=409)
        self.request("POST", "/fdrs", {**payload, "institution_name": "  "}, expected=422)
        self.request("POST", "/fdrs", {**payload, "amount": -1}, expected=422)
        endpoint = f"/fdrs/{row['id']}"
        self.request("POST", endpoint + "/review", {"action": "verified"}, expected=400)
        self.request("POST", endpoint + "/submit")
        self.request("PUT", endpoint, payload, expected=400)
        self.request("POST", endpoint + "/review", {"action": "under_review"})
        self.request("POST", endpoint + "/review", {"action": "correction_required"}, expected=400)
        self.request("POST", endpoint + "/review", {"action": "correction_required", "review_note": "Fix bank"})
        updated = self.request("PUT", endpoint, {**payload, "bank_name": "Corrected Bank"})
        self.assertEqual(updated["bank_name"], "Corrected Bank")
        self.request("POST", endpoint + "/submit")
        self.request("POST", endpoint + "/review", {"action": "under_review"})
        self.request("POST", endpoint + "/review", {"action": "rejected", "review_note": "Not eligible"})
        self.assertEqual(self.request("GET", "/fdrs")[0]["status"], "Rejected")
        self.request("POST", endpoint + "/submit", expected=400)
        self.request("PUT", "/fdrs/9999", payload, expected=404)

    def upload(self, fdr_id, name, content, expected=201):
        boundary = "portal-test-boundary"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body += content + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", f"/fdrs/{fdr_id}/documents", expected=expected,
                            raw=body, content_type=f"multipart/form-data; boundary={boundary}")

    def test_documents(self):
        row, _ = self.register()
        formats = [("certificate.pdf", b"%PDF-1.4\nTest certificate"),
                   ("certificate.png", b"\x89PNG\r\n\x1a\nimage"),
                   ("certificate.jpg", b"\xff\xd8\xffimage")]
        for filename, content in formats:
            document = self.upload(row["id"], filename, content)
            downloaded = self.request("GET", f"/documents/{document['id']}/download")
            self.assertEqual(downloaded, content)
        self.assertEqual(len(self.request("GET", f"/fdrs/{row['id']}/documents")), 3)
        self.upload(row["id"], "bad.pdf", b"not a PDF", expected=400)
        self.upload(row["id"], "bad.exe", b"%PDF-1.4", expected=400)
        self.upload(row["id"], "big.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), expected=413)
        self.upload(9999, "missing.pdf", b"%PDF-1.4", expected=404)
        self.request("GET", "/documents/9999/download", expected=404)

    def test_renewal_history_and_conflicts(self):
        draft, _ = self.register("DRAFT")
        self.request("POST", f"/fdrs/{draft['id']}/renewal-requests", self.renewal(), expected=400)
        row, _ = self.register(verified=True)
        path = f"/fdrs/{row['id']}/renewal-requests"
        self.request("POST", path, {**self.renewal(), "maturity_date": date.today().isoformat()}, expected=400)
        request = self.request("POST", path, self.renewal(), expected=201)
        self.request("POST", path, self.renewal(), expected=409)
        self.request("POST", f"/fdrs/{row['id']}/release-requests", {"kind": "Refund", "reason": "Duplicate"}, expected=409)
        review_path = f"/renewal-requests/{request['id']}/review"
        self.request("POST", review_path, {"action": "approve"})
        self.request("POST", review_path, {"action": "approve"}, expected=409)
        record = next(record for record in self.request("GET", "/fdrs") if record["id"] == row["id"])
        self.assertEqual(record["certificate_number"], "NEW-001")
        self.assertEqual(record["status"], "Verified")
        history = self.request("GET", "/renewal-requests")[0]
        self.assertEqual(history["previous_certificate_number"], "TEST-001")
        self.assertEqual(history["status"], "Approved")
        request = self.request("POST", path, self.renewal("Replacement"), expected=201)
        review_path = f"/renewal-requests/{request['id']}/review"
        self.request("POST", review_path, {"action": "reject"}, expected=400)
        self.request("POST", review_path, {"action": "reject", "review_note": "Wrong certificate"})
        self.assertEqual(self.request("GET", "/renewal-requests")[0]["status"], "Rejected")

    def test_certificate_conflict_at_renewal_approval(self):
        row, _ = self.register(verified=True)
        request = self.request("POST", f"/fdrs/{row['id']}/renewal-requests", self.renewal(), expected=201)
        self.register("NEW-001")
        self.request("POST", f"/renewal-requests/{request['id']}/review", {"action": "approve"}, expected=409)
        self.assertEqual(self.request("GET", "/renewal-requests")[0]["status"], "Pending")
        self.assertEqual(len(self.request("GET", "/renewal-requests")[0]["timeline"]), 1)

    def test_release_refund_and_alerts(self):
        draft, _ = self.register("DRAFT")
        self.request("POST", f"/fdrs/{draft['id']}/release-requests", {"kind": "Release", "reason": "Completed"}, expected=400)
        for kind, final_status in (("Release", "Released"), ("Refund", "Refunded")):
            row, _ = self.register(kind, verified=True)
            path = f"/fdrs/{row['id']}/release-requests"
            data = {"kind": kind, "reason": "Institution obligations completed"}
            request = self.request("POST", path, data, expected=201)
            review_path = f"/release-requests/{request['id']}/review"
            self.request("POST", review_path, {"action": "reject"}, expected=400)
            self.request("POST", review_path, {"action": "reject", "review_note": "More evidence needed"})
            self.assertEqual(next(record for record in self.request("GET", "/fdrs") if record["id"] == row["id"])["status"], "Verified")
            request = self.request("POST", path, data, expected=201)
            self.request("POST", path, data, expected=409)
            self.request("POST", f"/release-requests/{request['id']}/review", {"action": "approve"})
            self.request("POST", f"/release-requests/{request['id']}/review", {"action": "reject", "review_note": "Late"}, expected=409)
            records = self.request("GET", "/fdrs")
            self.assertEqual(next(record for record in records if record["id"] == row["id"])["status"], final_status)
            self.request("POST", path, data, expected=400)
        self.assertEqual(self.request("GET", "/fdrs/alerts")["total_alerts"], 0)
        self.register("ALERT", verified=True)
        self.assertEqual(self.request("GET", "/fdrs/alerts")["total_alerts"], 1)
        self.assertEqual(len(self.request("GET", "/release-requests")), 4)

    def test_release_history_preserves_original_details(self):
        row, _ = self.register(verified=True)
        release = self.request("POST", f"/fdrs/{row['id']}/release-requests",
                               {"kind": "Refund", "reason": "Initial request"}, expected=201)
        self.request("POST", f"/release-requests/{release['id']}/review",
                     {"action": "reject", "review_note": "Renew first"})
        renewal = self.request("POST", f"/fdrs/{row['id']}/renewal-requests", self.renewal(), expected=201)
        self.request("POST", f"/renewal-requests/{renewal['id']}/review", {"action": "approve"})
        history = self.request("GET", "/release-requests")[0]
        self.assertEqual(history["certificate_number"], "TEST-001")
        self.assertEqual(history["amount"], 100000)

    def test_intelligence_boundaries_and_weighted_score(self):
        self.assertEqual(self.request("GET", "/intelligence")["risk_score"], 0)
        with sqlite3.connect(self.api.DB_PATH) as connection:
            for index, days in enumerate((-1, 0, 7, 8, 30, 31, 90, 91)):
                connection.execute("""INSERT INTO fdrs
                    (institution_name, certificate_number, bank_name, amount, maturity_date, status)
                    VALUES ('College', ?, 'Bank', 100, ?, 'Verified')""",
                    (f"RISK-{index}", (date.today() + timedelta(days=days)).isoformat()))
            for status in ("Draft", "Released", "Refunded", "Rejected"):
                connection.execute("""INSERT INTO fdrs
                    (institution_name, certificate_number, bank_name, amount, maturity_date, status)
                    VALUES ('College', ?, 'Bank', 999999, ?, ?)""", (status, date.today().isoformat(), status))
        result = self.request("GET", "/intelligence")
        self.assertEqual(result["categories"], {"Critical": 3, "High": 2, "Upcoming": 2, "Safe": 1})
        self.assertEqual([window["amount"] for window in result["windows"]], [200, 400, 600])
        self.assertEqual(result["overdue_principal"], 100)
        self.assertEqual(result["risk_score"], 60)
        self.assertEqual(result["records"][0]["suggested_action"], "Initiate release")
        self.assertEqual(result["records"][2]["suggested_action"], "Review renewal")
        for days, expected_amount in ((7, 200), (30, 400), (90, 600)):
            answer = self.request("POST", "/assistant/query", {"action": f"maturing_{days}"})
            self.assertEqual(answer["amount"], expected_amount)
        with sqlite3.connect(self.api.DB_PATH) as connection:
            connection.execute("UPDATE fdrs SET amount=300 WHERE certificate_number='RISK-7'")
        self.assertEqual(self.request("GET", "/intelligence")["risk_score"], 48)

    def test_request_audit_stages_and_open_request_conflicts(self):
        for request_type in ("renewal", "release"):
            row, _ = self.register(f"AUDIT-{request_type}", verified=True)
            data = self.renewal() if request_type == "renewal" else {"kind": "Release", "reason": "Obligations complete"}
            request = self.request("POST", f"/fdrs/{row['id']}/{request_type}-requests",
                                   {**data, "actor": "College registrar"}, expected=201)
            path = f"/{request_type}-requests/{request['id']}/review"
            self.request("POST", path, {"action": "start_review", "actor": "Officer Rao", "review_note": "Checking certificate"})
            self.request("POST", path, {"action": "start_review"}, expected=409)
            self.request("POST", f"/fdrs/{row['id']}/release-requests",
                         {"kind": "Refund", "reason": "Conflicting request"}, expected=409)
            self.request("POST", path, {"action": "reject"}, expected=400)
            history = self.request("GET", f"/{request_type}-requests")[0]
            self.assertEqual(history["status"], "Under Review")
            self.assertEqual(len(history["timeline"]), 2)
            self.request("POST", path, {"action": "reject", "actor": "Officer Rao", "review_note": "Missing evidence"})
            events = self.request("GET", f"/{request_type}-requests")[0]["timeline"]
            self.assertEqual([event["decision"] for event in events], ["Submitted", "Under Review", "Rejected"])
            self.assertEqual(events[0]["actor"], "College User (college)")
            self.assertEqual(events[-1]["actor"], "Review Officer (officer)")
            self.assertEqual(events[-1]["remarks"], "Missing evidence")
            self.assertTrue(all(event["created_at"] for event in events))
            self.api.initialize_database()
            self.assertEqual(len(self.request("GET", f"/{request_type}-requests")[0]["timeline"]), 3)

    def test_legacy_request_events_backfill_without_inventing_review(self):
        row, _ = self.register(verified=True)
        request = self.request("POST", f"/fdrs/{row['id']}/release-requests",
                               {"kind": "Refund", "reason": "Legacy submission"}, expected=201)
        self.request("POST", f"/release-requests/{request['id']}/review",
                     {"action": "reject", "review_note": "Legacy decision"})
        with sqlite3.connect(self.api.DB_PATH) as connection:
            connection.execute("DELETE FROM request_events")
        self.api.initialize_database()
        self.api.initialize_database()
        history = self.request("GET", "/release-requests")[0]
        self.assertEqual([event["decision"] for event in history["timeline"]], ["Submitted", "Rejected"])
        self.assertEqual(history["timeline"][0]["created_at"], history["created_at"])
        self.assertEqual(history["timeline"][1]["created_at"], history["reviewed_at"])
        self.assertTrue(all(event["actor"] == "Legacy actor unknown" for event in history["timeline"]))

    def test_assistant_bank_grouping_and_ties(self):
        for certificate, bank in (("BANK-A", "Metro Bank"), ("BANK-B", " metro bank "), ("BANK-C", "Other Bank")):
            row, _ = self.register(certificate, verified=True)
            with sqlite3.connect(self.api.DB_PATH) as connection:
                connection.execute("UPDATE fdrs SET bank_name=?, amount=? WHERE id=?",
                                   (bank, 200000 if bank == "Other Bank" else 100000, row["id"]))
        answer = self.request("POST", "/assistant/query", {"action": "top_bank"})
        self.assertEqual(answer["amount"], 400000)
        self.assertEqual(answer["count"], 3)
        self.assertIn("tied", answer["answer"])
        with sqlite3.connect(self.api.DB_PATH) as connection:
            connection.execute("UPDATE fdrs SET amount=300000 WHERE certificate_number='BANK-A'")
        answer = self.request("POST", "/assistant/query", {"action": "top_bank"})
        self.assertEqual(answer["amount"], 400000)
        self.assertEqual(answer["count"], 2)
        self.assertNotIn("tied", answer["answer"])

    def test_assistant_queries_use_database(self):
        self.assertEqual(self.request("POST", "/assistant/query", {"action": "top_bank"})["count"], 0)
        row, _ = self.register(verified=True)
        for question in ("How much money matures in the next 30 days?", "Which FDRs need immediate attention?",
                         "Which bank holds the highest principal amount?"):
            answer = self.request("POST", "/assistant/query", {"question": question})
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["amount"], 100000)
            self.assertEqual(answer["count"], 1)
            self.assertEqual(answer["records"][0]["id"], row["id"])
        release = self.request("POST", f"/fdrs/{row['id']}/release-requests",
                               {"kind": "Release", "reason": "Obligations complete"}, expected=201)
        self.request("POST", f"/release-requests/{release['id']}/review", {"action": "start_review"})
        answer = self.request("POST", "/assistant/query", {"question": "Show all pending release requests."})
        self.assertEqual(answer["count"], 1)
        self.assertEqual(answer["records"][0]["status"], "Under Review")
        self.request("POST", f"/release-requests/{release['id']}/review", {"action": "approve"})
        for action in ("attention", "pending_releases", "maturing_30", "top_bank"):
            self.assertEqual(self.request("POST", "/assistant/query", {"action": action})["amount"], 0)
        answer = self.request("POST", "/assistant/query", {"question": "Delete my records"})
        self.assertFalse(answer["supported"])
        self.assertIsNone(answer["amount"])

    def test_additive_migration_preserves_existing_record(self):
        legacy = Path(self.workspace.name) / "legacy.db"
        with sqlite3.connect(legacy) as connection:
            connection.execute("""CREATE TABLE fdrs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_name TEXT NOT NULL, certificate_number TEXT NOT NULL UNIQUE,
                bank_name TEXT NOT NULL, amount REAL NOT NULL, maturity_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Draft')""")
            connection.execute("INSERT INTO fdrs VALUES (42, 'Legacy College', 'OLD', 'Bank', 100, '2027-01-01', 'Verified')")
        self.api.DB_PATH = legacy
        self.api.initialize_database()
        self.api.initialize_database()
        self.request("POST", "/auth/login", {"username": "college", "password": "Test-college-12345", "role": "college"}, role="college")
        records = self.request("GET", "/fdrs")
        self.assertEqual(records[0]["id"], 42)
        self.assertEqual(records[0]["certificate_number"], "OLD")
        self.assertIsNone(records[0]["review_note"])


if __name__ == "__main__":
    unittest.main()