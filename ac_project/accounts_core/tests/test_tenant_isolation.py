import datetime
import json
from decimal import Decimal

import pytest
from django.test import RequestFactory, TestCase

from accounts_core.models import Company, Currency, Invoice
from accounts_core.views import invoice_list


class TenantIsolationManagerTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.company_a = Company.objects.create(
            name="Company A", default_currency=self.usd
        )
        self.company_b = Company.objects.create(
            name="Company B", default_currency=self.usd, slug="com_b"
        )

        # create one invoice per company
        self.inv_a = Invoice.objects.create(
            invoice_number="A-1",
            company=self.company_a,
            date=datetime.date.today(),
            total=Decimal("200.00"),
        )

        self.inv_b = Invoice.objects.create(
            invoice_number="B-1",
            company=self.company_b,
            date=datetime.date.today(),
            total=Decimal("100.00"),
        )

    def test_for_company_returns_only_that_company_objects(self):
        """Compare invoice primary keys"""
        # Compares two lists, element by element
        self.assertListEqual(
            #  Call custom manager method for_company() to
            #  filter queryset by company foreign key
            list(
                Invoice.objects.for_company(self.company_a)
                .order_by("id")
                .values_list("pk", flat=True)
            ),
            [self.inv_a.pk],  # expected result
        )

        self.assertListEqual(
            list(
                Invoice.objects.for_company(self.company_b)
                .order_by("id")
                .values_list("pk", flat=True)
            ),
            [self.inv_b.pk],
        )

    def test_get_other_company_object_raises_does_not_exist(self):
        # `for_company` shouldn't return the other company's record
        with self.assertRaises(Invoice.DoesNotExist):
            Invoice.objects.for_company(self.company_a).get(pk=self.inv_b.pk)


@pytest.mark.django_db
def test_invoice_list_returns_only_tenant_data(client, django_user_model):
    usd = Currency.objects.create(code="USD", name="US Dollar")
    c1 = Company.objects.create(
        name="Company A", default_currency=usd, slug="com_a")
    c2 = Company.objects.create(
        name="Company B", default_currency=usd, slug="com_b")
    u1 = django_user_model.objects.create_user(username="alice", password="pw")

    Invoice.objects.create(
        company=c1,
        description="C1 invoice",
        date=datetime.date.today(),
        total=Decimal("100.00"),
    )
    Invoice.objects.create(
        company=c2,
        description="C2 invoice",
        date=datetime.date.today(),
        total=Decimal("200.00"),
    )

    # Bypass client & call view with a RequestFactory
    request = RequestFactory().get("/invoices/")
    request.user = u1
    # attach company to request before hitting view
    request.company = c1  # manually simulate middleware

    # call the view directly
    response = invoice_list(request)
    data = json.loads(response.content)

    descriptions = [d["description"] for d in data]
    assert "C1 invoice" in descriptions  # available in c1 request
    assert "C2 invoice" not in descriptions  # not available in c2 request
