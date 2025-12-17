from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from ..services.audit_helper import log_action
# Import models
from ..models import (Account, FixedAsset, Period, JournalEntry, JournalLine)


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
        4. Cap depreciation so accumulated_depreciation never exceeds purchase_cost.
        5. Set status to 'capitalized' if it was 'draft' (first depreciation).
        6. Return created JournalEntry.  
    """
    with transaction.atomic():
        # lock asset row
        asset = FixedAsset.objects.select_for_update().get(pk=asset_id)
        period = Period.objects.get(pk=period_id)

        if not asset.useful_life_years or asset.useful_life_years <= 0:
            raise ValidationError("Asset must have a valid useful life")

        # calculate straight-line (per-year) depreciation and quantize to cents
        per_period = (asset.purchase_cost / Decimal(asset.useful_life_years)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # remaining book value
        remaining = (asset.purchase_cost - asset.accumulated_depreciation).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # If nothing left to depreciate, abort
        if remaining <= Decimal("0.00"):
            raise ValidationError("Asset already fully depreciated")

        # actual amount to record this run (cap to remaining)
        depreciation_amount = per_period if per_period <= remaining else remaining

        if depreciation_amount <= Decimal("0.00"):
            raise ValidationError("Computed depreciation amount is zero")

        # Fetch GL accounts 
        # Account for Depreciation Expense
        expense_acct = Account.objects.get(company=asset.company, code="6400")
        # Account for Accumulated Depreciation
        accum_dep_acct = Account.objects.get(company=asset.company, code="1220")

        # depreciation entry + lines are either all committed or all rolled back
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
                description=f"Depreciation Expense for {asset.asset_code or asset.id}",
                debit_original=depreciation_amount,    
                credit_original=Decimal("0.00"),
                currency=je.company.default_currency,
                fixed_asset=asset,
            )

        JournalLine.objects.create(
                company=asset.company,
                journal=je,
                account=accum_dep_acct,
                description=f"Accumulated Depreciation for {asset.asset_code or asset.id}",
                debit_original=Decimal("0.00"),
                credit_original=depreciation_amount,
                currency=je.company.default_currency,
                fixed_asset=asset,
            )

        # 3. Post (validate balance)
        je.post(user=user)

        # Update the asset
        asset.accumulated_depreciation = (
            (asset.accumulated_depreciation + depreciation_amount)
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

        # On first depreciation, set to 'capitalized' 
        if asset.status == "draft":
            asset.status = "capitalized"

        asset.save(update_fields=["accumulated_depreciation", "status"])

        # Record an audit log entry
        log_action(
            action="depreciate", 
            instance=asset, 
            user=user, 
            changes={"Status": str(asset.status), 
                     "Accumulated depreciation": str(asset.accumulated_depreciation)}
        )
        return je