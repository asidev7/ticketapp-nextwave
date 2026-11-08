from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import SiteSettings
from .qrcodes import buy_page_url


@override_settings(SITE_URL="https://nextwaveparty.com")
class BuyTicketQRCodeTests(TestCase):
    def setUp(self):
        self.site = SiteSettings.load()
        self.png_url = reverse("admin:core_sitesettings_download_buy_qr_png", args=[self.site.pk])
        self.svg_url = reverse("admin:core_sitesettings_download_buy_qr_svg", args=[self.site.pk])

    def test_qr_points_to_short_buy_url(self):
        self.assertEqual(buy_page_url(), "https://nextwaveparty.com/buy/")

    def test_buy_url_opens_ticket_section(self):
        response = self.client.get(reverse("core:buy"))
        self.assertRedirects(response, "/#tickets", fetch_redirect_response=False)

    def test_anonymous_visitor_cannot_download(self):
        response = self.client.get(self.png_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_staff_without_permission_cannot_download(self):
        User.objects.create_user("door", "door@example.com", "pw", is_staff=True)
        self.client.login(username="door", password="pw")
        self.assertEqual(self.client.get(self.png_url).status_code, 403)

    def test_admin_downloads_png_and_svg(self):
        User.objects.create_superuser("boss", "boss@example.com", "pw")
        self.client.login(username="boss", password="pw")

        png = self.client.get(self.png_url)
        self.assertEqual(png.status_code, 200)
        self.assertEqual(png["Content-Type"], "image/png")
        self.assertIn("attachment", png["Content-Disposition"])
        self.assertEqual(png.content[:8], b"\x89PNG\r\n\x1a\n")

        svg = self.client.get(self.svg_url)
        self.assertEqual(svg.status_code, 200)
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", svg.content)
