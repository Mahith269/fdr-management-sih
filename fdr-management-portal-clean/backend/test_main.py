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

    def request(self, method, path, data=None, expected=200, raw=None, content_type=None):
        headers = {}
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
            payload = response.read()
            self.assertEqual(response.status, expected, payload[:1000])
            if response.headers.get("Content-Type", "").startswith("application/json"):
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
        records = self.request("GET", "/fdrs")
        self.assertEqual(records[0]["id"], 42)
        self.assertEqual(records[0]["certificate_number"], "OLD")
        self.assertIsNone(records[0]["review_note"])


if __name__ == "__main__":
    unittest.main()