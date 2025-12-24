from django.contrib import admin
from .ReadOnly import ReadOnlyAdmin
from ..models.matview import (
    JournalLineAggPeriod,
    TrialBalancePeriod,
    TrialBalanceRunning,
    ProfitLossPeriod,
    BalanceSheetRunning,
)

@admin.register(JournalLineAggPeriod)
class JournalLineAggPeriodAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "company_id",
        "period_id",
        "last_txn_date",
        "account_code",
        "account_name",
        "account_type",
        "net_amount_original",
    )


@admin.register(TrialBalancePeriod)
class TrialBalancePeriodAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "company_id",
        "period_id",
        "account_id",
        "account_code",
        "account_name",
        "account_type",
        "period_debit_original",
        "period_credit_original",
        "period_balance_original",
        "period_debit_local",
        "period_credit_local",
        "period_balance_local"
        )


@admin.register(TrialBalanceRunning)
class TrialBalanceRunningAdmin(ReadOnlyAdmin):
     list_display = (
        "id",
        "company_id",
        "account_id",
        "account_code",
        "account_name",
        "account_type",
        "total_debit_to_date_original",
        "total_credit_to_date_original",
        "balance_to_date_original",
        "total_debit_to_date_local",
        "total_credit_to_date_local",
        "balance_to_date_local"
       )


@admin.register(ProfitLossPeriod)
class ProfitLossPeriodAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "company_id",
        "period_id",
        "total_income_original",
        "total_expense_original",
        "net_profit_original",
        "total_income_local",
        "total_expense_local",
        "net_profit_local"
    )


@admin.register(BalanceSheetRunning)
class BalanceSheetRunningAdmin(ReadOnlyAdmin):
     list_display = (
        "id",
        "company_id",
        "account_id",
        "account_code",
        "account_name",
        "account_type",
        "balance_to_date_original",
        "balance_to_date_local"
    )
