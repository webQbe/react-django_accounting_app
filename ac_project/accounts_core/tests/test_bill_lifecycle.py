from decimal import Decimal
import datetime
from django.test import TestCase
from django.core.exceptions import ValidationError
from ..services import post_bill, pay_bill, apply_bank_tx_to_bill
from ..models import Company, Currency, Bill, BillLine, Item, Account, BankAccount, BankTransaction


class BillLifecycleTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.company = Company.objects.create(name="Test Co", default_currency=self.usd)
        self.account = Account.objects.create(company=self.company, code="1140", name="Inventory", ac_type="Asset", normal_balance="debit")
        self.item = Item.objects.create(company=self.company, sku="SKU-1", name="Widget", default_unit_price=Decimal("10.00"))
        self.bank_account = BankAccount.objects.create(company=self.company, name="Bank A")
        self.bt = BankTransaction.objects.create(company=self.company, bank_account=self.bank_account, payment_date=datetime.date(2025, 9, 18), amount=Decimal("100.00"), currency_code="USD")
    
    def make_bill(self, lines=None, total=Decimal("0.00"), outstanding_amount=None):
        """
        Helper to create an bill. `lines` is a list of (description, amount) tuples.
        """
        bill = Bill.objects.create(
            company=self.company,
            bill_number="BILL-001",
            total=Decimal(total),
            outstanding_amount=(Decimal(outstanding_amount) if outstanding_amount is not None else Decimal(total)),
            status="draft",
            date=datetime.date.today(),
        )

        # Create lines if provided
        if lines:
            for idx, (desc, amt) in enumerate(lines, start=1):
                BillLine.objects.create(
                    bill=bill,
                    item=self.item,
                    account=self.account,
                    description=desc,
                    quantity=1,
                    unit_price=Decimal(amt),
                    line_total=Decimal(amt),
                )
        return bill

    def test_post_bill_requires_at_least_one_line(self):
        bill = self.make_bill(lines=None, total=Decimal("100.00"), outstanding_amount=Decimal("100.00"))

        # calling post_bill() on an bill with no lines should raise ValidationError
        with self.assertRaises(ValidationError):
            post_bill(bill)

        # status unchanged
        bill.refresh_from_db()
        self.assertEqual(bill.status, "draft")

    def test_successful_post_and_pay_flow_and_paid_immutability(self):
        # Create bill with one line of 100
        bill = self.make_bill(lines=[("Item", "100.00")], total=Decimal("100.00"), outstanding_amount=Decimal("100.00"))

        # post must succeed now
        bill = post_bill(bill)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "posted")

        # cannot pay while outstanding != 0
        with self.assertRaises(ValidationError):
            pay_bill(bill)

        # Simulate payment that clears outstanding 
        apply_bank_tx_to_bill(self.bt.id, [{"bill_id": bill.id, "amount": bill.outstanding_amount}])

        bill.save() # calls full_clean() & raises ValidationError if it would be invalid
        
        # Now pay should succeed
        bill = pay_bill(bill)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "paid")

        # Attempt to change an immutable field (bill_number or total)
        # should raise ValidationError
        bill.bill_number = "BILL-CHANGED"
        with self.assertRaises(ValidationError):
            bill.save() # triggers model-level immutability

    def test_invalid_transition_directly_raises(self):
        bill = self.make_bill(lines=[("Item", "10.00")], total=Decimal("10.00"), outstanding_amount=Decimal("10.00"))
        # Trying to go straight from draft -> paid should be forbidden by transition_to
        with self.assertRaises(ValidationError):
            bill.transition_to("paid")

        # status unchanged
        bill.refresh_from_db()
        self.assertEqual(bill.status, "draft")

    def test_pay_requires_outstanding_zero(self):
        bill = self.make_bill(lines=[("Item", "50.00")], total=Decimal("50.00"), outstanding_amount=Decimal("25.00"))

        # Move to post first (post_bill enforces lines)
        bill = post_bill(bill)
        self.assertEqual(bill.status, "posted")

        # pay_bill must fail because outstanding != 0
        with self.assertRaises(ValidationError):
            pay_bill(bill)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "posted")
