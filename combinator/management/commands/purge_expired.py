from django.core.management.base import BaseCommand

from combinator.services import purge_expired


class Command(BaseCommand):
    help = "Delete rendered variants past their download window and uploads of abandoned drafts."

    def handle(self, *args, **options):
        expired, abandoned = purge_expired()
        self.stdout.write(f"{expired} variantes expiradas, {abandoned} proyectos abandonados purgados.")
