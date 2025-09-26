from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seeds the database with demo data (wraps create_demo_tenant)."

    # Define command-line argument
    def add_arguments(self, parser):
        parser.add_argument(
            "--company",  # Define flag
            type=str,
            default="Demo Ltd",
            help="Name of the demo company (default: Demo Ltd)",
        )

    def handle(self, *args, **options):
        com_name = options["company"]  # Read argument from add_arguments()

        # Call your existing command
        self.stdout.write(self.style.NOTICE(
            f"Seeding demo data for {com_name}..."))
        call_command("create_demo_tenant", company_name=com_name)
        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully!"))
