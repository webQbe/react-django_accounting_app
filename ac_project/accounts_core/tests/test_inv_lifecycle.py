from decimal import Decimal
import datetime
from django.test import TestCase
from django.core.exceptions import ValidationError
from ..services import open_invoice, pay_invoice, apply_bank_tx_to_inv
from ..models import Company, Currency, Invoice, InvoiceLine, Item, Account, BankAccount, BankTransaction


class InvoiceLifecycleTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.company = Company.objects.create(name="Test Co", default_currency=self.usd)
        self.account = Account.objects.create(company=self.company, code="1140", name="Inventory", ac_type="Asset", normal_balance="debit")
        self.item = Item.objects.create(company=self.company, sku="SKU-1", name="Widget", default_unit_price=Decimal("10.00"))
        self.bank_account = BankAccount.objects.create(company=self.company, name="Bank A")
        self.bt = BankTransaction.objects.create(company=self.company, bank_account=self.bank_account, payment_date=datetime.date(2025, 9, 18), amount=Decimal("100.00"), currency_code="USD")
    
    def make_invoice(self, lines=None, total=Decimal("0.00"), outstanding_amount=None):
        """
        Helper to create an invoice. `lines` is a list of (description, amount) tuples.
        """
        invoice = Invoice.objects.create(
            company=self.company,
            invoice_number="INV-001",
            total=Decimal(total),
            outstanding_amount=(Decimal(outstanding_amount) if outstanding_amount is not None else Decimal(total)),
            status="draft",
            date=datetime.date.today(),
        )

        # Create lines if provided
        if lines:
            for idx, (desc, amt) in enumerate(lines, start=1):
                InvoiceLine.objects.create(
                    invoice=invoice,
                    item=self.item,
                    account=self.account,
                    description=desc,
                    quantity=1,
                    unit_price=Decimal(amt),
                    line_total=Decimal(amt),
                )
        return invoice

    def test_open_invoice_requires_at_least_one_line(self):
        invoice = self.make_invoice(lines=None, total=Decimal("100.00"), outstanding_amount=Decimal("100.00"))

        # calling open_invoice() on an invoice with no lines should raise ValidationError
        with self.assertRaises(ValidationError):
            open_invoice(invoice)

        # status unchanged
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")

    def test_successful_open_and_pay_flow_and_paid_immutability(self):
        # Create invoice with one line of 100
        invoice = self.make_invoice(lines=[("Item", "100.00")], total=Decimal("100.00"), outstanding_amount=Decimal("100.00"))

        # open must succeed now
        invoice = open_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "open")

        # cannot pay while outstanding != 0
        with self.assertRaises(ValidationError):
            pay_invoice(invoice)

        # Simulate payment that clears outstanding 
        apply_bank_tx_to_inv(self.bt.id, [{"invoice_id": invoice.id, "amount": invoice.outstanding_amount}])

        invoice .save() # calls full_clean() & raises ValidationError if it would be invalid
        
        # Now pay should succeed
        invoice = pay_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")

        # Attempt to change an immutable field (invoice _number or total)
        # should raise ValidationError
        invoice.invoice_number = "INV-CHANGED"
        with self.assertRaises(ValidationError):
            invoice.save() # triggers model-level immutability

    def test_invalid_transition_directly_raises(self):
        invoice = self.make_invoice(lines=[("Item", "10.00")], total=Decimal("10.00"), outstanding_amount=Decimal("10.00"))
        # Trying to go straight from draft -> paid should be forbidden by transition_to
        with self.assertRaises(ValidationError):
            invoice.transition_to("paid")

        # status unchanged
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")

    def test_pay_requires_outstanding_zero(self):
        invoice = self.make_invoice(lines=[("Item", "50.00")], total=Decimal("50.00"), outstanding_amount=Decimal("25.00"))

        # Move to open first (open_invoice enforces lines)
        invoice = open_invoice(invoice)
        self.assertEqual(invoice.status, "open")

        # pay_invoice must fail because outstanding != 0
        with self.assertRaises(ValidationError):
            pay_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "open")
