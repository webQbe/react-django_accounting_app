import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase

from ..models import (BankAccount, BankTransaction, BankTransactionInvoice,
                      Company, Currency, Invoice)
from ..services import apply_bank_tx_to_inv


class ApplyBankTxTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.company = Company.objects.create(
            name="Test Co", default_currency=self.usd)

        # a bank account that belongs to the company and uses USD
        self.bank_account = BankAccount.objects.create(
            company=self.company, name="Bank A"
        )

        # bank transaction for $100
        self.bt = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_account,
            payment_date=datetime.date(2025, 9, 17),
            amount=Decimal("100.00"),
            currency_code="USD",
        )

        # two invoices with outstanding amounts
        self.inv1 = Invoice.objects.create(
            company=self.company,
            date=datetime.date(2025, 9, 17),
            total=Decimal("200.00"),
            outstanding_amount=Decimal("200.00"),
        )
        self.inv2 = Invoice.objects.create(
            company=self.company,
            date=datetime.date(2025, 9, 17),
            total=Decimal("150.00"),
            outstanding_amount=Decimal("150.00"),
        )

    def test_prevent_over_apply(self):
        """
        If total requested application > bank transaction amount,
        service should raise (ValidationError or custom) and
        no BankTransactionInvoice rows should be persisted.
        """
        # try to apply 60 + 50 + 10 = 120 (> 100)
        invoice_applications = [
            {"invoice_id": self.inv1.id, "amount": Decimal("60.00")},
            {"invoice_id": self.inv2.id, "amount": Decimal("50.00")},
            {"invoice_id": self.inv1.id, "amount": Decimal("10.00")},
        ]

        # Expect validation error (BankTransaction.clean() or
        # apply_payment should detect over-apply)
        with self.assertRaises(ValidationError):
            apply_bank_tx_to_inv(self.bt.id, invoice_applications)

        # Ensure no partial allocations were recorded
        btinv = BankTransactionInvoice
        self.assertEqual(
            btinv.objects.filter(bank_transaction=self.bt).count(),
            0,
            msg="No BankTransactionInvoice rows should be "
            "persisted when over-applying",
        )

        # Ensure invoices' outstanding amounts were not changed
        self.inv1.refresh_from_db()
        self.inv2.refresh_from_db()
        self.assertEqual(self.inv1.outstanding_amount, Decimal("200.00"))
        self.assertEqual(self.inv2.outstanding_amount, Decimal("150.00"))

    def test_atomicity_on_failure_mid_loop(self):
        """
        If one apply in the middle raises (e.g. invoice does not exist),
        earlier successful applies must be rolled back.
        """
        # valid first application,
        # second references a non-existent invoice id -> will raise
        invoice_applications = [
            {   # would succeed
                "invoice_id": self.inv1.id, "amount": Decimal("40.00")
            },
            {  # non-existent -> causes failure
                "invoice_id": 9999999,
                "amount": Decimal("50.00"),
            },
        ]

        # narrow to the actual exception your apply_payment raises
        # (Invoice.DoesNotExist or ValidationError)
        with self.assertRaises(Exception):
            apply_bank_tx_to_inv(self.bt.id, invoice_applications)

        # Nothing persisted for this bank transaction
        btinv = BankTransactionInvoice
        self.assertEqual(
            btinv.objects.filter(bank_transaction=self.bt).count(),
            0,
            msg="Partial allocations should be rolled back on failure",
        )

        # invoice1 outstanding must remain unchanged
        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.outstanding_amount, Decimal("200.00"))
