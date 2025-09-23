from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from decimal import Decimal
import datetime
from django.utils.text import slugify
from accounts_core.models import (
    Company,
    Account,
    Invoice,
    JournalEntry,
    JournalLine,
    BankTransaction,
    Currency,
    Customer,
    BankAccount,
)


User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create a demo tenant (company), user, and sample financial data for testing."
    )

    # Define command-line arguments
    def add_arguments(self, parser):
        parser.add_argument(
            "--company-name",  # Define flag
            default="Demo Company",
            help="Name of the demo company to create.",
        )
        parser.add_argument(
            "--username", default="demo", help="Username for the demo user."
        )
        parser.add_argument(
            "--password", default="demo123", help="Password for the demo user."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Read arguments from add_arguments()
        company_name = options["company_name"]
        username = options["username"]
        password = options["password"]

        # Generate unique slug for company
        def unique_slug_for_company(name, max_tries=100):
            # Convert company name into a slug (e.g., "Test Ltd" → "test-ltd")
            base = (
                slugify(name) or "company"
            )  # fall back to "company" if empty string is returned
            slug = base
            i = 1  # add numbers if needed
            # If plain slug is taken, append -1, -2, etc.
            while Company.objects.filter(slug=slug).exists():
                slug = (
                    f"{base}-{i}"  # Example: "test-ltd" → "test-ltd-1" → "test-ltd-2"
                )
                i += 1  # Move to next number if slug is still not unique
                if (
                    i > max_tries
                ):  # if we try 100 times and still can’t find a free slug
                    raise RuntimeError(
                        "Couldn't generate unique slug"
                    )  # bail out with an error
            return slug

        # 1. Create company
        usd, created = Currency.objects.get_or_create(
            code="USD", defaults={"name": "US Dollar"}
        )

        slug = unique_slug_for_company(company_name)

        # get_or_create returns (object, created)
        # object → actual Company instance (existing or new).
        # created → boolean flag (True if newly created, False if reused)
        company, _ = (
            Company.objects.get_or_create(  # ignore `created` boolean flag by naming it `_`
                # Look for Company where name=company_name
                name=company_name,
                defaults={"default_currency": usd, "slug": slug},
                # If it exists: get that company object back.
                # If it doesn’t: create new company with that name.
            )
        )
        # print text to console
        self.stdout.write(
            self.style.SUCCESS(  # make message green
                # insert __str__() representation of Company model
                f"Created company: {company}"
            )
        )

        # 2. Create user
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@example.com",
            },
        )
        if created:  # if user newly created
            user.set_password(password)
            user.save()
        # assume you have user.company = FK to Company
        user.company = company
        user.save()
        self.stdout.write(
            self.style.SUCCESS(f"Created user: {user.username} (pw={password})")
        )

        # 3. Create sample accounts
        cash, _ = Account.objects.get_or_create(
            company=company,
            code="1110",
            defaults={"name": "Cash", "ac_type": "Asset", "normal_balance": "debit"},
        )
        revenue, _ = Account.objects.get_or_create(
            company=company,
            code="4000",
            defaults={
                "name": "Revenue",
                "ac_type": "Income",
                "normal_balance": "credit",
            },
        )
        self.stdout.write(self.style.SUCCESS("Created accounts (Cash, Revenue)"))

        # 4. Create sample invoice
        self.customer = Customer.objects.create(company=company, name="Test Customer")
        invoice = Invoice.objects.create(
            company=company,
            invoice_number="INV-001",
            date=datetime.date.today(),
            total=Decimal("1000.00"),
            customer=self.customer,
        )
        self.stdout.write(
            self.style.SUCCESS(f"Created invoice: {invoice.invoice_number}")
        )

        # 5. Create journal entry + lines
        journal = JournalEntry.objects.create(
            company=company,
            date=datetime.date.today(),
            description="Invoice posting",
            status="draft",
        )

        JournalLine.objects.create(
            journal=journal,
            company=company,
            account=cash,
            currency=usd,
            debit_original=Decimal("1000.00"),
            credit_original=Decimal("0.00"),
        )
        JournalLine.objects.create(
            journal=journal,
            company=company,
            account=revenue,
            currency=usd,
            debit_original=Decimal("0.00"),
            credit_original=Decimal("1000.00"),
        )
        journal.post()

        self.stdout.write(self.style.SUCCESS("Created journal entry with lines"))

        # 6. Create bank transaction

        self.bank_account = BankAccount.objects.create(company=company, name="Bank A")

        bank_tx = BankTransaction.objects.create(
            company=company,
            bank_account=self.bank_account,
            payment_date=datetime.date.today(),
            amount=Decimal("1000.00"),
            currency_code="USD",
            description="Payment received",
        )
        self.stdout.write(self.style.SUCCESS("Created bank transaction"))
        self.stdout.write(self.style.SUCCESS("Demo tenant setup complete!"))
