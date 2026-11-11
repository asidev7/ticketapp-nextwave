"""python manage.py seed_demo

Loads fixtures/demo.json — the event of the official poster (XMAS with Q-Dot,
Dec 25 & 26, 2026, J5 Lounge), its line-up, 3 ticket types, FAQ and site
settings — and copies the poster (fixtures/poster.jpg) to the media folder.
Optionally creates a scanner account.
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand

DEMO_DIR = Path(settings.MEDIA_ROOT) / "demo"
FIXTURES = Path(settings.BASE_DIR) / "fixtures"


class Command(BaseCommand):
    help = "Load the event content of the official poster."

    def add_arguments(self, parser):
        parser.add_argument("--scanner-email", help="Also create a scanner account with this e-mail.")
        parser.add_argument("--scanner-password", help="Password of the scanner account.")
        parser.add_argument("--skip-images", action="store_true", help="Do not copy the poster image.")

    def handle(self, *args, **options):
        if not options["skip_images"]:
            DEMO_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / "poster.jpg", DEMO_DIR / "poster-xmas.jpg")
        call_command("loaddata", str(FIXTURES / "demo.json"), verbosity=0)
        self.stdout.write(self.style.SUCCESS("Event content loaded: 1 event (2 days), 34 artists, 3 ticket types, 8 FAQ."))

        group, _created = Group.objects.get_or_create(name=settings.SCANNER_GROUP_NAME)
        email, password = options.get("scanner_email"), options.get("scanner_password")
        if email and password:
            User = get_user_model()
            user, created = User.objects.get_or_create(username=email.split("@")[0], defaults={"email": email})
            user.email = email
            user.set_password(password)
            user.save()
            user.groups.add(group)
            self.stdout.write(self.style.SUCCESS(f"Scanner account {'created' if created else 'updated'}: {email}"))

        self.stdout.write("Next: configure the payment gateways and the scanner accesses in the admin, then open http://localhost:8000/")
