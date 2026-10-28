"""Keep the no-build frontend coherent across updates and existing browser caches."""

import hashlib
import re
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


class StudioStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        if path in ("", ".", "index.html") and scope["method"] in ("GET", "HEAD"):
            root = Path(self.directory)
            html = (root / "index.html").read_text(encoding="utf-8")

            def version_asset(match):
                url = match.group(2)
                asset = (root / url).resolve()
                if not asset.is_relative_to(root.resolve()) or not asset.is_file():
                    return match.group(0)
                version = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
                return f'{match.group(1)}="{url}?v={version}"'

            html = re.sub(r'(src|href)="([^"?]+\.(?:js|css))"', version_asset, html)
            response = HTMLResponse(html, headers={"Cache-Control": "no-cache"})
            if scope["method"] == "HEAD":
                response.body = b""
            return response

        response = await super().get_response(path, scope)
        # Include 304 responses: a validated cache must revalidate next time too.
        if path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache"
        return response
