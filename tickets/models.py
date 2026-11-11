import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import COUNTRY_CHOICES, CURRENCY_CHOICES, NGN
from core.models import Event, TranslatedMixin, hex_color_validator
from core.storage import private_storage
from core.utils import format_money

from .qr import build_qr_payload


class TicketType(TranslatedMixin, models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ticket_types", verbose_name=_("event"))
    name_en = models.CharField(_("name (EN)"), max_length=60)
    name_fr = models.CharField(_("name (FR)"), max_length=60, blank=True)
    slug = models.SlugField(_("slug"), unique=True)
    description_en = models.TextField(_("description (EN)"), blank=True)
    description_fr = models.TextField(_("description (FR)"), blank=True)
    price_xof = models.PositiveIntegerField(_("price (XOF)"), default=0)
    price_ngn = models.PositiveIntegerField(_("price (NGN)"), default=0)
    capacity = models.PositiveIntegerField(_("capacity"), default=100)
    sold = models.PositiveIntegerField(_("sold"), default=0, help_text=_("Paid tickets (updated automatically)."))
    reserved = models.PositiveIntegerField(_("reserved"), default=0, help_text=_("Held by unpaid orders for 15 minutes."))
    is_active = models.BooleanField(_("active"), default=True)
    sale_start = models.DateTimeField(_("sale starts"), null=True, blank=True)
    sale_end = models.DateTimeField(_("sale ends"), null=True, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)
    color = models.CharField(_("badge colour"), max_length=7, default="#C8102E", validators=[hex_color_validator])
    seat_image = models.ImageField(_("spot / zone image"), upload_to="tickets/seats/", blank=True)
    zone_label = models.CharField(_("zone label"), max_length=100, blank=True, help_text=_("e.g. Zone A – Front stage"))

    # Ticket design: your own artwork; the system adds the QR code, ticket code and holder.
    design_image = models.ImageField(
        _("ticket design"), upload_to="tickets/designs/", blank=True,
        help_text=_("Your own ticket artwork (one per ticket type), e.g. 2400 × 900 px. It keeps its proportions. "
                    "Leave a free area for the QR code: the system adds the QR code, the ticket code and the number. "
                    "Empty = automatic design in the poster style."),
    )
    qr_x = models.PositiveSmallIntegerField(
        _("QR position — horizontal (%)"), default=85, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Centre of the QR code from the left edge: 0 = left, 100 = right."),
    )
    qr_y = models.PositiveSmallIntegerField(
        _("QR position — vertical (%)"), default=50, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Centre of the QR code from the top edge: 0 = top, 100 = bottom."),
    )
    qr_size = models.PositiveSmallIntegerField(
        _("QR size (% of the height)"), default=56, validators=[MinValueValidator(15), MaxValueValidator(85)],
        help_text=_("Of the shorter side of the design."),
    )
    holder_overlay = models.CharField(
        _("holder name & photo"), max_length=12, default="bottom_left",
        choices=[("bottom_left", _("Bottom left")), ("top_left", _("Top left")), ("hidden", _("Hidden"))],
        help_text=_("Where the system writes the holder's photo and name on your design."),
    )

    class Meta:
        verbose_name = _("ticket type")
        verbose_name_plural = _("ticket types")
        ordering = ["order", "price_xof"]

    def __str__(self):
        return self.name_en

    @property
    def available(self) -> int:
        return max(self.capacity - self.sold - self.reserved, 0)

    @property
    def is_low_stock(self) -> bool:
        """Less than 20 % left: time to show "Only X left"."""
        return 0 < self.available <= max(10, self.capacity // 5)

    @property
    def is_sold_out(self) -> bool:
        return self.available <= 0

    def price_for(self, currency: str) -> int:
        return self.price_ngn if currency == NGN else self.price_xof

    def is_on_sale(self, now=None) -> bool:
        now = now or timezone.now()
        if not self.is_active or self.is_sold_out:
            return False
        if self.sale_start and now < self.sale_start:
            return False
        if self.sale_end and now > self.sale_end:
            return False
        return True

    def get_checkout_url(self):
        return reverse("tickets:checkout", args=[self.slug])


class TicketBenefit(TranslatedMixin, models.Model):
    ticket_type = models.ForeignKey(TicketType, on_delete=models.CASCADE, related_name="benefits", verbose_name=_("ticket type"))
    text_en = models.CharField(_("text (EN)"), max_length=150)
    text_fr = models.CharField(_("text (FR)"), max_length=150, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        verbose_name = _("benefit")
        verbose_name_plural = _("benefits")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.text_en


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    class StockState(models.TextChoices):
        RESERVED = "reserved", _("Reserved")
        SOLD = "sold", _("Sold")
        RELEASED = "released", _("Released")

    reference = models.CharField(_("reference"), max_length=24, unique=True, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="orders", verbose_name=_("event"))
    buyer_name = models.CharField(_("buyer name"), max_length=120)
    buyer_email = models.EmailField(_("buyer e-mail"), db_index=True)
    buyer_phone = models.CharField(_("buyer phone"), max_length=30, blank=True, db_index=True)
    country = models.CharField(_("country"), max_length=2, choices=COUNTRY_CHOICES)
    currency = models.CharField(_("currency"), max_length=3, choices=CURRENCY_CHOICES)
    subtotal = models.PositiveIntegerField(_("subtotal"), default=0)
    fees = models.PositiveIntegerField(_("fees"), default=0)
    total_amount = models.PositiveIntegerField(_("total"), default=0)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    stock_state = models.CharField(_("stock"), max_length=10, choices=StockState.choices, default=StockState.RESERVED, editable=False)
    gateway = models.ForeignKey(
        "payments.PaymentGateway", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="orders", verbose_name=_("payment gateway"),
    )
    gateway_transaction_id = models.CharField(_("gateway transaction ID"), max_length=100, blank=True, db_index=True)
    gateway_payload = models.JSONField(_("gateway payload"), default=dict, blank=True)
    holders = models.JSONField(_("ticket holders"), default=list, blank=True, editable=False)
    reserved_until = models.DateTimeField(_("reserved until"), null=True, blank=True)
    language = models.CharField(_("language"), max_length=5, default="en", choices=settings.LANGUAGES)
    is_complimentary = models.BooleanField(_("complimentary"), default=False)
    note = models.TextField(_("internal note"), blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    tickets_pdf = models.FileField(storage=private_storage, upload_to="tickets/orders/", blank=True, editable=False)
    last_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(_("created"), auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    def get_absolute_url(self):
        return reverse("tickets:order", args=[self.reference])

    @property
    def total_display(self) -> str:
        return format_money(self.total_amount, self.currency)

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID

    @property
    def quantity(self) -> int:
        return sum(item.quantity for item in self.items.all())

    @property
    def files_ready(self) -> bool:
        tickets = list(self.tickets.all())
        return bool(tickets) and all(t.ticket_png and t.ticket_pdf for t in tickets)


class ComplimentaryOrder(Order):
    """Proxy used by the admin to issue free tickets (guests, press, artists)."""

    class Meta:
        proxy = True
        verbose_name = _("complimentary ticket")
        verbose_name_plural = _("complimentary tickets")


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("order"))
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("ticket type"))
    quantity = models.PositiveSmallIntegerField(_("quantity"), default=1)
    unit_price = models.PositiveIntegerField(_("unit price"), default=0)

    class Meta:
        verbose_name = _("order item")
        verbose_name_plural = _("order items")

    def __str__(self):
        return f"{self.quantity} × {self.ticket_type}"

    @property
    def line_total(self) -> int:
        return self.quantity * self.unit_price


class Ticket(models.Model):
    class Status(models.TextChoices):
        VALID = "valid", _("Valid")
        USED = "used", _("Used")
        CANCELLED = "cancelled", _("Cancelled")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tickets", verbose_name=_("order"))
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name="tickets", verbose_name=_("ticket type"))
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(_("code"), max_length=20, unique=True, editable=False)
    serial = models.PositiveIntegerField(
        _("serial number"), null=True, blank=True, editable=False, db_index=True,
        help_text=_("Sequential number within the event, printed on the ticket."),
    )
    holder_name = models.CharField(_("holder name"), max_length=120, blank=True)
    holder_photo = models.ImageField(_("holder photo"), storage=private_storage, upload_to="holders/", blank=True)
    qr_token = models.CharField(_("QR content"), max_length=80, editable=False)
    qr_image = models.FileField(storage=private_storage, upload_to="tickets/qr/", blank=True, editable=False)
    ticket_png = models.FileField(storage=private_storage, upload_to="tickets/png/", blank=True, editable=False)
    ticket_pdf = models.FileField(storage=private_storage, upload_to="tickets/pdf/", blank=True, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.VALID, db_index=True)
    checked_in_at = models.DateTimeField(_("checked in at"), null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("checked in by"),
    )
    email_sent_at = models.DateTimeField(_("e-mail sent at"), null=True, blank=True)
    download_count = models.PositiveIntegerField(_("downloads"), default=0)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        verbose_name = _("ticket")
        verbose_name_plural = _("tickets")
        ordering = ["order", "pk"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if not self.qr_token:
            self.qr_token = build_qr_payload(self.uuid)
        super().save(*args, **kwargs)

    @property
    def initials(self) -> str:
        parts = [p for p in (self.holder_name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "NW"

    @property
    def serial_label(self) -> str:
        """'00042' — always five digits so printed numbers line up."""
        return f"{self.serial:05d}" if self.serial else ""


class PrintBatch(models.Model):
    """Tickets printed in bulk for physical sale (outlets, door): each one has
    its own signed QR code, code and serial number, like an online ticket."""

    class Status(models.TextChoices):
        GENERATING = "generating", _("Generating")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    class Paper(models.TextChoices):
        A4 = "a4", _("A4 sheets (several tickets per page, crop marks)")
        TICKET = "ticket", _("Ticket size (one ticket per page)")

    MAX_QUANTITY = 1000
    PER_PAGE_CHOICES = [(n, str(n)) for n in (2, 3, 4, 5, 6)]

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="print_batches", verbose_name=_("event"))
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name="print_batches", verbose_name=_("ticket type"))
    quantity = models.PositiveIntegerField(
        _("number of tickets"), validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY)],
    )
    outlet = models.ForeignKey(
        "core.PhysicalOutlet", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="print_batches", verbose_name=_("sold at"),
    )
    note = models.CharField(_("note"), max_length=200, blank=True)
    paper = models.CharField(_("paper format"), max_length=8, choices=Paper.choices, default=Paper.A4)
    per_page = models.PositiveSmallIntegerField(
        _("tickets per A4 page"), choices=PER_PAGE_CHOICES, default=4,
        help_text=_("4 or 5 for long tickets; the tickets are as large as the page allows."),
    )
    order = models.OneToOneField(Order, on_delete=models.PROTECT, null=True, editable=False, related_name="print_batch", verbose_name=_("order"))
    first_serial = models.PositiveIntegerField(_("first number"), null=True, editable=False)
    last_serial = models.PositiveIntegerField(_("last number"), null=True, editable=False)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.GENERATING, editable=False)
    pdf = models.FileField(storage=private_storage, upload_to="tickets/batches/", blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="+", verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        verbose_name = _("print batch")
        verbose_name_plural = _("print batches")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ticket_type} × {self.quantity} ({self.serial_range})"

    @property
    def serial_range(self) -> str:
        if not self.first_serial:
            return "—"
        return f"{self.first_serial:05d} → {self.last_serial:05d}"
