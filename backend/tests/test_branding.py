"""Branding (requirement 5.2): name and logos set by an admin, visible before sign-in."""
from __future__ import annotations

import io

SVG_OK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def _png(w=300, h=120) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (31, 95, 191, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_default_branding_is_public(client, client_as):
    anon = client_as()
    b = anon.get("/api/v1/branding").json()
    assert b["name"] == "估值工作台" and b["logo_light_url"] is None
    m = anon.get("/api/v1/branding/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    assert m.json()["icons"][0]["src"] == "/icon-192.png"


def test_admin_sets_name_and_png_logo_and_icons_follow(client, client_as):
    assert client.put("/api/v1/admin/branding", json={"name": "Hoi 投資研究"}).json()["name"] == "Hoi 投資研究"
    r = client.post("/api/v1/admin/branding/logo/light", files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["logo_light_url"].startswith("/api/v1/branding/logo/light?v=") and b["logo_dark_url"] == b["logo_light_url"]
    anon = client_as()
    assert anon.get(b["logo_light_url"]).headers["content-type"] == "image/png"
    man = anon.get("/api/v1/branding/manifest.webmanifest").json()
    assert man["name"] == "Hoi 投資研究" and man["icons"][0]["src"].startswith("/api/v1/branding/icon/192.png")
    from PIL import Image
    icon = Image.open(io.BytesIO(anon.get("/api/v1/branding/icon/512-maskable.png").content))
    assert icon.size == (512, 512)
    # removing the light logo falls back to the bundled icons
    assert client.delete("/api/v1/admin/branding/logo/light").json()["logo_light_url"] is None
    assert anon.get("/api/v1/branding/manifest.webmanifest").json()["icons"][0]["src"] == "/icon-192.png"


def test_svg_logo_is_served_with_a_script_blocking_policy(client, client_as):
    r = client.post("/api/v1/admin/branding/logo/dark", files={"file": ("logo.svg", SVG_OK, "image/svg+xml")})
    url = r.json()["logo_dark_url"]
    resp = client_as().get(url)
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_unsafe_or_oversized_logos_are_refused(client):
    bad = [b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
           b"GIF89a....", b"\x89PNG\r\n\x1a\n" + b"broken", b"<svg>" + b" " * (512 * 1024)]
    for data in bad:
        r = client.post("/api/v1/admin/branding/logo/light", files={"file": ("x", data, "application/octet-stream")})
        assert r.status_code == 400, data[:40]
    assert client.post("/api/v1/admin/branding/logo/sepia", files={"file": ("x.svg", SVG_OK, "image/svg+xml")}).status_code == 400
    assert client.put("/api/v1/admin/branding", json={"name": ""}).status_code == 422


def test_members_cannot_change_branding(member):
    assert member.put("/api/v1/admin/branding", json={"name": "x"}).status_code == 403
    assert member.post("/api/v1/admin/branding/logo/light",
                       files={"file": ("logo.svg", SVG_OK, "image/svg+xml")}).status_code == 403
