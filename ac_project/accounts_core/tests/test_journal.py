from django.apps import apps
from django.utils import timezone
from django.test import TestCase
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from accounts_core.models import Company, JournalEntry, JournalLine, Currency, Account, User
from ..exceptions import UnbalancedJournalError, AlreadyPostedDifferentPayload, CompanyMismatchError

""" Success tests """
class JournalEntrySuccessTests(TestCase):

    def setUp(self):
        # create a currency
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        # setup company
        self.company = Company.objects.create(name="Test Co", default_currency= self.usd)
        # setup debit account
        self.cash = Account.objects.create(
            company=self.company,
            code="1110", 
            name="Cash on Hand",
            ac_type="Asset",
            normal_balance = "debit"
        )
        # setup credit account
        self.revenue = Account.objects.create(
            company=self.company,
            code="4000", 
            name="Operating Revenue",
            ac_type="Income",
            normal_balance = "credit"
        )

        # create JournalEntry
        self.je = JournalEntry.objects.create(
                                              company=self.company, 
                                              date="2025-09-15", 
                                              status="draft"
                                            )
        
        # Arrange: create a balanced journal entry
        JournalLine.objects.create(
                                   journal=self.je, 
                                   company=self.company,
                                   account=self.cash,
                                   currency=self.usd, 
                                   debit_original=100, 
                                   credit_original=0, 
                                )
        JournalLine.objects.create(
                                   journal=self.je, 
                                   company=self.company,
                                   account=self.revenue,
                                   currency=self.usd, 
                                   debit_original=0, 
                                   credit_original=100, 
                                )


    """ Test Balanced Entry """
    def test_balanced_entry_posts_successfully(self):

        self.je.post() # Post JournalEntry

        self.je.refresh_from_db() # get up-to-date values
        
        # check if posting logic actually changed journal status
        self.assertEqual(self.je.status, "posted") 
       
        # check if at least one line has been marked `is_posted=True`
        self.assertTrue(self.je.lines.filter(is_posted=True).exists())


    """ Test for Idempotency 
          1. You create a balanced journal entry.
          2. Call .post() once → journal gets posted.
          3. Call .post() again → nothing new should happen 
           (no duplicate lines, no status change).
    """
    def test_post_is_idempotent_when_called_twice_with_same_data(self):

        # First call → should post successfully
        self.je.post()
        self.je.refresh_from_db()
        self.assertEqual(self.je.status, "posted")

        # Save fingerprint + lines
        first_fp = self.je.posting_fingerprint
        first_lines = list(self.je.lines.values_list("id", "debit_original", "credit_original"))

        # Second call → should be a no-op (idempotent)
        self.je.post()
        self.je.refresh_from_db()

        second_fp = self.je.posting_fingerprint
        second_lines = list(self.je.lines.values_list("id", "debit_original", "credit_original"))

        # Assertions
        self.assertEqual(first_fp, second_fp)  # same fingerprint
        self.assertEqual(first_lines, second_lines)  # no extra lines created
        self.assertEqual(self.je.status, "posted")  # still posted
        self.assertEqual(self.je.lines.count(), 2)  # only 2 lines, no duplicates


""" Failure tests """
class JournalEntryFailureTests(TestCase):

    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
       
        # setup company 1
        self.company1 = Company.objects.create(name="Test Co", default_currency=self.usd)
        # setup debit account 1
        self.cash_a = Account.objects.create(company=self.company1, code="1110", name="Cash on Hand", ac_type="Asset", normal_balance = "debit")
       
        self.revenue = Account.objects.create(company=self.company1, code="4000", name="Operating Revenue", ac_type="Income", normal_balance = "credit")
        self.je = JournalEntry.objects.create(company=self.company1, date="2025-09-15", status="draft")
        self.user = User.objects.create()
        # Create Debit entry
        JournalLine.objects.create(
                                    journal=self.je, 
                                    company=self.company1, 
                                    account=self.cash_a, 
                                    currency=self.usd, 
                                    debit_original=100, 
                                    credit_original=0
                                )


    """ Test for Unbalanced Entry """
    def test_unbalanced_entry_cannot_be_posted(self):
        
        # Raise custom exception 
        with self.assertRaises(UnbalancedJournalError) as cm:
            self.je.post()

        self.assertIn("Journal not balanced", str(cm.exception))


    def test_post_atomicity_on_failure(self):

        # capture starting JournalLine count
        before = JournalLine.objects.filter(journal=self.je).count()
        # Try posting → should raise UnbalancedJournalError
        with self.assertRaises(UnbalancedJournalError):
            self.je.post(user=self.user)
        # Refresh from DB to observe post-call/rolled-back state
        self.je.refresh_from_db()

        """ Checks to confirm the entry was not transformed into a posted/immutable state. """
        # 1) Ensure JE was not marked as posted
        self.assertEqual(self.je.status, "draft", 
                         "JournalEntry.status should remain 'draft'")
        
        # 2) Ensure posted_at/posted_by/posting_fingerprint should not be set
        self.assertIsNone(getattr(self.je, "posted_at", None))
        self.assertIsNone(getattr(self.je, "posting_fingerprint", None))

        # 3) Assert JournalLine count is unchanged
        after = JournalLine.objects.filter(journal=self.je).count()
        self.assertEqual(after, before, 
                            "JournalLine count should not change after failed post()")


    def test_post_enforces_tenant_scope(self):

        # setup company 2
        self.company2 = Company.objects.create(name="Test Co 2", slug="test_co2", default_currency=self.usd)
        
        # setup debit account 2
        self.cash_b = Account.objects.create(
            company=self.company2,
            code="2110", 
            name="Cash B",
            ac_type="Asset",
            normal_balance = "debit"
        )

        # Creating a JournalLine whose company differs from the JournalEntry's company
        # should raise ValidationError immediately
        with self.assertRaises(ValidationError):
            # Invalid credit line (company2 mismatch!)
            JournalLine.objects.create(
                journal=self.je,
                company=self.company2,  # Wrong company
                account=self.cash_b,
                currency=self.usd,
                debit_original=0,
                credit_original=100,
            )

        # Ensure journal was NOT marked as posted
        self.je.refresh_from_db() # Refresh JE and assert posting did not happen and input lines unchanged
        # original valid line from setUp should still be present
        self.assertEqual(self.je.status, "draft")

        # the original valid line from setUp should still be present
        self.assertEqual(self.je.lines.count(), 1)

   
    def test_post_raises_if_already_posted_and_data_changed(self):

        """ Test if `AlreadyPostedDifferentPayload` is raised when 
        a JournalEntry has already been posted and someone tries to 
        change its data (lines, amounts, etc.) and post again  """

        # Create Credit entry for balancing
        JournalLine.objects.create(journal=self.je, company=self.company1, account=self.revenue, currency=self.usd, debit_original=0, credit_original=100)

        # First post succeeds
        self.je.post(user=self.user)

        # Mutate data (simulate tampering after posting)
        # Keep totals balanced
        debitline = self.je.lines.first()
        debitline.debit_original = Decimal("150.00") # Change debit line from 100 → 150
        creditline = self.je.lines.last()
        # Change credit line from 100 → 150
        creditline.credit_original = Decimal("150.00") 
        debitline.save()
        creditline.save()

        # Still Balanced: It won’t fail the balance check (since 100 = 100, or 150 = 150).
        # But the payload is different than what was originally posted
        # Second post should now raise
        with self.assertRaises(AlreadyPostedDifferentPayload):
            self.je.post(user=self.user)
