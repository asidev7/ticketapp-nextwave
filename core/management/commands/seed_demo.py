"""python manage.py seed_demo

Loads fixtures/demo.json (1 event, 4 hero slides, 10 artists, 3 ticket
types, FAQ, outlets, site settings) and draws the placeholder images it uses.
Optionally creates a scanner account.
"""
import random
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFilter

from tickets.render import font

DEMO_DIR = Path(settings.MEDIA_ROOT) / "demo"
NAVY = (10, 61, 145)
DEEP = (11, 27, 58)
ORANGE = (255, 107, 0)
WHITE = (255, 255, 255)
DARK = (17, 24, 39)

HERO_PALETTES = [
    ((11, 27, 58), (255, 107, 0), (59, 130, 246)),
    ((30, 10, 60), (236, 72, 153), (255, 160, 0)),
    ((5, 40, 40), (16, 185, 129), (250, 204, 21)),
    ((40, 10, 10), (249, 115, 22), (99, 102, 241)),
]
ARTIST_COLORS = [
    (10, 61, 145), (180, 83, 9), (21, 128, 61), (126, 34, 206), (190, 18, 60),
    (3, 105, 161), (15, 118, 110), (161, 98, 7), (67, 56, 202), (17, 24, 39),
]


def _stage_scene(size, palette, seed):
    """Concert-like abstract scene: light beams, bokeh and a crowd silhouette."""
    rng = random.Random(seed)
    w, h = size
    base, beam, glow = palette
    img = Image.new("RGB", size, base)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(7):
        x = w * (0.1 + 0.8 * i / 6)
        spread = rng.randint(w // 14, w // 7)
        color = beam if i % 2 else glow
        draw.polygon([(x, -10), (x - spread, h), (x + spread, h)], fill=(*color, rng.randint(40, 80)))
    for _ in range(40):
        r = rng.randint(6, 40)
        cx, cy = rng.randint(0, w), rng.randint(0, int(h * 0.6))
        color = rng.choice([beam, glow, WHITE])
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, rng.randint(30, 90)))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(overlay, (0, 0), overlay)
    crowd = ImageDraw.Draw(img)
    floor = int(h * 0.82)
    crowd.rectangle([0, floor, w, h], fill=(0, 0, 0))
    x = -20
    while x < w + 20:
        head = rng.randint(18, 30)
        top = floor - rng.randint(30, 90)
        crowd.ellipse([x, top, x + head * 2, top + head * 2], fill=(0, 0, 0))
        crowd.rectangle([x - 8, top + head * 2 - 6, x + head * 2 + 8, h], fill=(0, 0, 0))
        if rng.random() < 0.35:  # raised hand
            hx = x + head + rng.randint(-10, 10)
            crowd.line([(hx, top + head), (hx + rng.randint(-30, 30), top - rng.randint(40, 90))], fill=(0, 0, 0), width=14)
        x += head * 2 + rng.randint(4, 20)
    return img


def _poster(size):
    w, h = size
    img = _stage_scene(size, HERO_PALETTES[0], 99)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, int(h * 0.42)], fill=DEEP)
    draw.rectangle([60, 70, 60 + 330, 130], fill=ORANGE)
    draw.text((80, 78), "1ST EDITION", font=font(40, True), fill=DARK)
    draw.text((60, 170), "NEXTWAVE", font=font(170, True), fill=WHITE)
    draw.text((60, 350), "PARTY", font=font(170, True), fill=ORANGE)
    draw.rectangle([0, h - 230, w, h], fill=WHITE)
    draw.text((60, h - 200), "SAT · DEC 27, 2026 · 8:00 PM", font=font(52, True), fill=DARK)
    draw.text((60, h - 120), "PALAIS DES CONGRÈS · COTONOU", font=font(44), fill=DARK)
    return img


def _artist(index, name):
    size = 600
    color = ARTIST_COLORS[index % len(ARTIST_COLORS)]
    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)
    for i in range(6):
        r = 120 + i * 70
        draw.ellipse([size / 2 - r, size * 0.42 - r, size / 2 + r, size * 0.42 + r], outline=tuple(min(255, c + 40) for c in color), width=6)
    draw.ellipse([210, 120, 390, 300], fill=DEEP)
    draw.rounded_rectangle([120, 300, 480, 640], radius=150, fill=DEEP)
    initials = "".join(part[0] for part in name.split()[:2]).upper()
    fnt = font(64, True)
    bbox = draw.textbbox((0, 0), initials, font=fnt)
    draw.text(((size - (bbox[2] - bbox[0])) / 2, 430), initials, font=fnt, fill=WHITE)
    return img


ZONES = {
    "vvip": ("A", "Front stage", (300, 190, 900, 300)),
    "vip": ("B", "VIP lounge", (80, 190, 280, 700)),
    "regular": ("C", "Main floor", (300, 320, 1120, 700)),
}


def _hall(highlight=None):
    w, h = 1200, 800
    img = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([20, 20, w - 20, h - 20], radius=20, outline=(107, 114, 128), width=4)
    draw.rounded_rectangle([300, 50, 900, 160], radius=12, fill=DARK)
    draw.text((520, 82), "STAGE", font=font(44, True), fill=WHITE)
    draw.rounded_rectangle([920, 190, 1120, 300], radius=12, fill=(229, 231, 235))
    draw.text((960, 228), "BAR", font=font(32, True), fill=DARK)
    draw.rectangle([540, h - 24, 660, h - 16], fill=(22, 163, 74))
    draw.text((548, h - 64), "ENTRANCE", font=font(22, True), fill=DARK)
    for key, (letter, label, box) in ZONES.items():
        active = highlight is None or key == highlight
        fill = {"vvip": (17, 24, 39), "vip": (180, 83, 9), "regular": NAVY}[key] if active else (229, 231, 235)
        text = WHITE if active else (75, 85, 99)
        draw.rounded_rectangle(box, radius=14, fill=fill)
        x0, y0, x1, y1 = box
        draw.text((x0 + 20, y0 + 16), f"ZONE {letter}", font=font(42, True), fill=text)
        draw.text((x0 + 20, y0 + 66), label, font=font(32), fill=text)
        if highlight == key:
            draw.rounded_rectangle([x0 + 20, y1 - 64, x0 + 250, y1 - 16], radius=24, fill=ORANGE)
            draw.text((x0 + 40, y1 - 56), "YOUR SPOT", font=font(28, True), fill=DARK)
    return img


ARTIST_NAMES = [
    "Ayo Marina", "Kemi Sogbe", "Tobi Lagoon", "Senan", "DJ Wavy K",
    "DJ Hounkpe", "DJ Nala", "MC Fifame", "Wave Crew", "Surprise Guest",
]


class Command(BaseCommand):
    help = "Load the NextWave Party demo content and generate its images."

    def add_arguments(self, parser):
        parser.add_argument("--scanner-email", help="Also create a scanner account with this e-mail.")
        parser.add_argument("--scanner-password", help="Password of the scanner account.")
        parser.add_argument("--skip-images", action="store_true", help="Do not (re)generate demo images.")

    def handle(self, *args, **options):
        if not options["skip_images"]:
            self.generate_images()
        call_command("loaddata", str(Path(settings.BASE_DIR) / "fixtures" / "demo.json"), verbosity=0)
        self.stdout.write(self.style.SUCCESS("Demo content loaded: 1 event, 4 slides, 10 artists, 3 ticket types, 6 FAQ."))

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

        self.stdout.write("Next: configure the payment gateways in the admin (Payment gateways) and open http://localhost:8000/")

    def generate_images(self):
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        for i, palette in enumerate(HERO_PALETTES, start=1):
            _stage_scene((1600, 900), palette, seed=i).save(DEMO_DIR / f"hero-{i}.jpg", quality=78, optimize=True, progressive=True)
        _stage_scene((1200, 900), HERO_PALETTES[1], seed=21).save(DEMO_DIR / "about.jpg", quality=78, optimize=True)
        _stage_scene((1280, 720), HERO_PALETTES[3], seed=42).save(DEMO_DIR / "video-cover.jpg", quality=78, optimize=True)
        _poster((1200, 1700)).save(DEMO_DIR / "poster.jpg", quality=82, optimize=True)
        for i, name in enumerate(ARTIST_NAMES, start=1):
            _artist(i - 1, name).save(DEMO_DIR / f"artist-{i}.jpg", quality=80, optimize=True)
        _hall().save(DEMO_DIR / "hall-map.png", optimize=True)
        for key in ZONES:
            _hall(highlight=key).save(DEMO_DIR / f"seat-{key}.png", optimize=True)
        self.stdout.write(f"Demo images written to {DEMO_DIR}")
