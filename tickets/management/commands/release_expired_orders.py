from django.core.management.base import BaseCommand

from tickets.services import release_expired_orders


class Command(BaseCommand):
    help = "Cancel pending orders whose 15-minute reservation expired (cron fallback when Celery beat is not running)."

    def handle(self, *args, **options):
        count = release_expired_orders()
        self.stdout.write(self.style.SUCCESS(f"{count} expired order(s) released."))
