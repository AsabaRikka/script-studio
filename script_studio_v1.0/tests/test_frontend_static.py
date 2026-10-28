import re
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend_static import StudioStaticFiles


class FrontendCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "index.html").write_text(
            '<link href="studio.css"><script src="manual-editor.js"></script>', encoding="utf-8")
        (self.root / "studio.css").write_text("body {color:black}", encoding="utf-8")
        (self.root / "manual-editor.js").write_text("window.version='old'", encoding="utf-8")
        app = FastAPI()
        app.mount("/", StudioStaticFiles(directory=self.root, html=True))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_old_page_validators_cannot_hide_updated_script(self):
        old = self.client.get("/")
        old_script = re.search(r'src="([^"]+)"', old.text)[1]
        old_css = re.search(r'href="([^"]+)"', old.text)[1]
        (self.root / "manual-editor.js").write_text("window.version='new'", encoding="utf-8")
        # An already-open browser can supply validators from the previous static HTML.
        new = self.client.get("/", headers={"If-None-Match": '"old-html"',
                                           "If-Modified-Since": "Wed, 31 Dec 2099 23:59:59 GMT"})
        new_script = re.search(r'src="([^"]+)"', new.text)[1]
        self.assertEqual(new.status_code, 200)
        self.assertEqual(new.headers["cache-control"], "no-cache")
        self.assertNotEqual(new_script, old_script)
        self.assertIn(old_css, new.text)
        self.assertEqual(self.client.get(new_script).text, "window.version='new'")
        self.assertEqual(self.client.get("/index.html").text, new.text)

    def test_static_200_and_304_require_revalidation(self):
        for url in ("/manual-editor.js", "/studio.css"):
            response = self.client.get(url)
            cached = self.client.get(url, headers={"If-None-Match": response.headers["etag"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(response.headers["cache-control"], "no-cache")
            self.assertEqual(cached.headers["cache-control"], "no-cache")

    def test_head_keeps_get_headers_without_body(self):
        get = self.client.get("/")
        head = self.client.head("/")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], get.headers["content-length"])
        self.assertEqual(head.headers["cache-control"], "no-cache")


if __name__ == "__main__":
    unittest.main()
