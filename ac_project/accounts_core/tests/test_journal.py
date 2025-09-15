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

    def test_balanced_entry_posts_successfully(self):

        # Create Journal Lines: Debit 100, Credit 100
        JournalLine.objects.create(
                                   journal=self.je, 
                                   company=self.company, 
                                   account=self.account, 
                                   currency=self.usd, 
                                   debit_original=100, 
                                   credit_original=0
                                   )
        
        JournalLine.objects.create(journal=self.je, company=self.company, account=self.account, currency=self.usd, debit_original=0, credit_original=100)

        self.je.post() # Post JournalEntry

        self.je.refresh_from_db() # get up-to-date values
        
        # check if posting logic actually changed journal status
        self.assertEqual(self.je.status, "posted") 
       
        # check if at least one line has been marked `is_posted=True`
        self.assertTrue(self.je.journalline_set.filter(is_posted=True).exists())

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

    def test_unbalanced_entry_cannot_be_posted(self):

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

        # Raise custom exception 
        with self.assertRaises(UnbalancedJournalError) as cm:
            self.je.post()

        self.assertIn("Journal not balanced", str(cm.exception))
