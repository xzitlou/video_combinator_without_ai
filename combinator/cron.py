"""Periodic jobs. Run with: python manage.py rqcron combinator.cron"""

from rq import cron

from .tasks import purge_expired

# Downloads are refused as soon as expires_at passes; this deletes the files shortly after.
cron.register(purge_expired, "default", interval=5 * 60)
