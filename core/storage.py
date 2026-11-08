from django.conf import settings
from django.core.files.storage import FileSystemStorage


def private_storage():
    """Storage for holder photos and generated tickets.

    Files live outside MEDIA_ROOT and have no public URL: they are only
    served through views protected by signed tokens or staff permissions.
    """
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)
