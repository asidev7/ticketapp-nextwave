from django.db import models

from .crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """TextField whose value is encrypted at rest with Fernet.

    The Python value is always plain text; the database only ever sees the
    encrypted token. Do not filter on this field (tokens are non-deterministic).
    """

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt(value)
