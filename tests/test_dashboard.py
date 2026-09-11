# -*- coding: utf-8 -*-
"""Тесты панели обзора и разбора machines.json."""

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import (
    ShopState,
    build_fleet,
    load_machines_file,
    lookup_remote,
    start_dashboard,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(ROOT, "web")
EXAMPLE = os.path.join(ROOT, "machines.example.json")


class MachinesFileTests(unittest.TestCase):
    def test_example_file(self):
        title, remotes = load_machines_file(EXAMPLE)
        self.assertEqual(title, "Цех ЧПУ")
        self.assertEqual(len(remotes), 2)
        self.assertEqual(remotes[0]["id"], "edm-1")
        self.assertTrue(remotes[0]["url"].startswith("http://"))

    def test_skips_incomplete_rows(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(
                {
                    "title": "Тест",
                    "machines": [
                        {"id": "ok", "name": "Ок", "url": "http://127.0.0.1:9/"},
                        {"id": "", "url": "http://127.0.0.1:9"},
                        {"name": "без id", "url": "http://127.0.0.1:9"},
                    ],
                },
                fh,
            )
            path = fh.name
        try:
            title, remotes = load_machines_file(path)
        finally:
            os.remove(path)
        self.assertEqual(title, "Тест")
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0]["url"], "http://127.0.0.1:9")


class FleetTests(unittest.TestCase):
    def test_local_tile_without_remotes(self):
        state = ShopState()
        state.update_local({
            "id": "edm-local",
            "name": "Эрозия",
            "online": True,
            "X": "1",
            "has_data": True,
        })
        fleet = build_fleet(state)
        self.assertEqual(len(fleet["machines"]), 1)
        self.assertEqual(fleet["machines"][0]["name"], "Эрозия")
        self.assertIn("edm-local", fleet["machines"][0]["preview_url"])

    def test_dashboard_only_hides_local(self):
        state = ShopState()
        state.include_local = False
        fleet = build_fleet(state)
        self.assertEqual(fleet["machines"], [])

    def test_unreachable_remote_is_offline(self):
        state = ShopState()
        state.include_local = False
        with state.lock:
            state.remotes = [{
                "id": "down",
                "name": "Выкл",
                "url": "http://127.0.0.1:1",
            }]
        fleet = build_fleet(state)
        self.assertEqual(len(fleet["machines"]), 1)
        self.assertFalse(fleet["machines"][0]["online"])
        self.assertEqual(fleet["machines"][0]["name"], "Выкл")

    def test_lookup_remote(self):
        state = ShopState()
        with state.lock:
            state.remotes = [{"id": "edm-1", "name": "A", "url": "http://192.168.0.11:8080"}]
        self.assertIsNone(lookup_remote(state, "local"))
        self.assertEqual(lookup_remote(state, "edm-1")["url"], "http://192.168.0.11:8080")


class HttpDashboardTests(unittest.TestCase):
    def setUp(self):
        self.state = ShopState()
        self.state.update_local({
            "id": "local",
            "name": "Тестовый станок",
            "online": True,
            "window_found": True,
            "has_data": True,
            "X": "-10",
            "Y": "20",
        })
        self.state.preview = b"BM fake"
        self.server, self.thread, self.port = start_dashboard(
            self.state, "127.0.0.1", 0, WEB_ROOT
        )
        self.base = "http://127.0.0.1:%s" % self.port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()

    def test_index_and_assets(self):
        status, content_type, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("Обзор цеха ЧПУ".encode("utf-8"), body)

        status, content_type, body = self._get("/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", content_type)

        status, content_type, body = self._get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)

    def test_api_state_and_fleet(self):
        status, _, body = self._get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["name"], "Тестовый станок")
        self.assertEqual(data["X"], "-10")

        status, _, body = self._get("/api/fleet")
        fleet = json.loads(body.decode("utf-8"))
        self.assertEqual(len(fleet["machines"]), 1)
        self.assertEqual(fleet["machines"][0]["Y"], "20")

    def test_preview_and_traversal(self):
        status, content_type, body = self._get("/api/preview?machine=local")
        self.assertEqual(status, 200)
        self.assertIn("image/bmp", content_type)
        self.assertEqual(body, b"BM fake")

        try:
            urllib.request.urlopen(self.base + "/../../etc/passwd", timeout=3)
            self.fail("path traversal should not succeed")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (403, 404))


if __name__ == "__main__":
    unittest.main()
