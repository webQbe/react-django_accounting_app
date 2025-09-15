from django.test import TestCase
from accounts_core.models import Company, JournalEntry, JournalLine, Currency, Account
from ..exceptions import UnbalancedJournalError

""" Success tests """
class JournalEntrySuccessTests(TestCase):

    def setUp(self):
        # create a currency
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        # setup company
        self.company = Company.objects.create(name="Test Co", default_currency= self.usd)
        # setup account
        self.account = Account.objects.create(
            company=self.company,
            code="1234", 
            name="Fixed Assets",
            ac_type="Asset",
            normal_balance = "debit"
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
                                   account=self.account,
                                   currency=self.usd, 
                                   debit_original=100, 
                                   credit_original=0, 
                                )
        JournalLine.objects.create(
                                   journal=self.je, 
                                   company=self.company,
                                   account=self.account,
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
        # create a currency
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        # setup company
        self.company = Company.objects.create(name="Test Co", default_currency= self.usd)
        # setup account
        self.account = Account.objects.create(
            company=self.company,
            code="1234", 
            name="Fixed Assets",
            ac_type="Asset",
            normal_balance = "debit"
        )

        # Create JournalEntry
        self.je = JournalEntry.objects.create(
                    company=self.company, 
                    date="2025-09-15", 
                    status="draft"
                ) 
        
        # Create Journal Line: Credit 100
        JournalLine.objects.create(
                    journal=self.je, 
                    company=self.company, 
                    account=self.account, 
                    currency=self.usd, 
                    debit_original=0, 
                    credit_original=100
                )

    """ Test for Unbalanced Entry """
    def test_unbalanced_entry_cannot_be_posted(self):
        
        # Raise custom exception 
        with self.assertRaises(UnbalancedJournalError) as cm:
            self.je.post()

        self.assertIn("Journal not balanced", str(cm.exception))
