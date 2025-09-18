from decimal import Decimal
import datetime
from django.test import TestCase
from accounts_core.models import Company, Invoice, Currency

class TenantIsolationManagerTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.company_a = Company.objects.create(name="Company A", default_currency=self.usd)
        self.company_b = Company.objects.create(name="Company B", default_currency=self.usd, slug="com_b")

        # create one invoice per company
        self.inv_a = Invoice.objects.create(invoice_number="A-1", company=self.company_a, 
                                            date=datetime.date.today(), 
                                            total=Decimal("200.00"))
        
        self.inv_b = Invoice.objects.create(invoice_number="B-1", company=self.company_b, 
                                            date=datetime.date.today(), 
                                            total=Decimal("100.00"))

    def test_for_company_returns_only_that_company_objects(self):

        """ Compare invoice primary keys """
        # Compares two lists, element by element
        self.assertListEqual(
            # Call custom manager method for_company() to filter queryset by company foreign key
            list(Invoice.objects.for_company(self.company_a).order_by('id').values_list('pk', flat=True)),   
            [self.inv_a.pk] # expected result
        )

        self.assertListEqual(
            list(Invoice.objects.for_company(self.company_b).order_by('id').values_list('pk', flat=True)),
            [self.inv_b.pk]
        )


    def test_get_other_company_object_raises_does_not_exist(self):
        # `for_company` shouldn't return the other company's record
        with self.assertRaises(Invoice.DoesNotExist):
            Invoice.objects.for_company(self.company_a).get(pk=self.inv_b.pk)
