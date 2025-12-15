from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
# Import models
from ..models import (Account, FixedAsset)


# ----------------------------
# Fixed Asset workflows
# ----------------------------
def depreciate_asset(asset_id, period_id, user=None):
    """
    Record depreciation for a fixed asset into the ledger.
    Workflow:
      1. Calculate depreciation for the period.
      2. Create a JournalEntry (if not already posted).
      3. Add JournalLines:
         - Debit Depreciation Expense
         - Credit Accumulated Depreciation.
    """
    from ..models import Period
    asset = FixedAsset.objects.select_for_update().get(pk=asset_id)
    period = Period.objects.get(pk=period_id)

    if not asset.useful_life_years or asset.useful_life_years <= 0:
        raise ValidationError("Asset must have a valid useful life")

    # Straight-line depreciation for simplicity
    depreciation_amount = asset.purchase_cost / asset.useful_life_years

    # Account for Depreciation Expense
    expense_acct = Account.objects.get(company=asset.company, code="6000")
    # Account for Accumulated Depreciation
    accum_dep_acct = Account.objects.get(company=asset.company, code="1500")

    # depreciation entry + lines are either all committed or all rolled back
    with transaction.atomic():
        from ..models import JournalEntry, JournalLine
        # 1. Create JournalEntry header
        je_desc = f"Depreciation for asset {asset.asset_code or asset.id}"
        je = JournalEntry.objects.create(
            company=asset.company,
            period=period,
            date=timezone.now().date(),
            description=je_desc,
            status="draft",
            created_by=user,
            source_type="FixedAsset",  # link back to FixedAsset
            source_id=asset.id,
        )

        # 2. Add JournalLines
        JournalLine.objects.create(
            company=asset.company,
            journal=je,
            account=expense_acct,
            description="Depreciation Expense",
            debit_amount=depreciation_amount,
            credit_amount=Decimal("0.00"),
            fixed_asset=asset,
        )

        JournalLine.objects.create(
            company=asset.company,
            journal=je,
            account=accum_dep_acct,
            description="Accumulated Depreciation",
            debit_amount=Decimal("0.00"),
            credit_amount=depreciation_amount,
            fixed_asset=asset,
        )

        # 3. Post (validates balance)
        je.post(user=user)

    return je