from django.db import models

# ------------------------ Materialized Views ----------------------

"""  Base aggregation by company/period/account """


class JournalLineAggPeriod(models.Model):
    # Field types must line up with materialized view’s columns
    company_id = models.IntegerField()
    period_id = models.IntegerField()
    last_txn_date = models.DateField()
    account_id = models.IntegerField()
    account_code = models.CharField(max_length=50)
    account_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50)
    total_debit_original = models.DecimalField(max_digits=18, decimal_places=2)
    total_credit_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    net_amount_original = models.DecimalField(max_digits=18, decimal_places=2)
    total_debit_local = models.DecimalField(max_digits=18, decimal_places=2)
    total_credit_local = models.DecimalField(max_digits=18, decimal_places=2)
    net_amount_local = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        managed = False  # Django won’t try to create/drop this
        db_table = "mv_jl_agg_period"  # must match the materialized view name
        verbose_name = "Journal Line Period Aggregate"
        verbose_name_plural = "Journal Line Period Aggregate Records"
        # mirror unique index from SQL
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "period_id", "account_id"],
                name="ux_mv_jl_agg_period_company_period_account",
            ),
        ]


""" Trial balance totals per period """


class TrialBalancePeriod(models.Model):
    company_id = models.IntegerField()
    period_id = models.IntegerField()
    account_id = models.IntegerField()
    account_code = models.CharField(max_length=50)
    account_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50)
    period_debit_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    period_credit_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    period_balance_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    period_debit_local = models.DecimalField(max_digits=18, decimal_places=2)
    period_credit_local = models.DecimalField(max_digits=18, decimal_places=2)
    period_balance_local = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        managed = False
        db_table = "mv_trial_balance_period"
        verbose_name = "Trial Balance Period"
        verbose_name_plural = "Trial Balance Period Records"
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "period_id", "account_id"],
                name="ux_mv_trial_balance_period_company_period_account",
            ),
        ]


""" Running Trial Balance (point-in-time) """


class TrialBalanceRunning(models.Model):
    company_id = models.IntegerField()
    account_id = models.IntegerField()
    account_code = models.CharField(max_length=50)
    account_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50)
    total_debit_to_date_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    total_credit_to_date_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    balance_to_date_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    total_debit_to_date_local = models.DecimalField(
        max_digits=18, decimal_places=2)
    total_credit_to_date_local = models.DecimalField(
        max_digits=18, decimal_places=2)
    balance_to_date_local = models.DecimalField(
        max_digits=18, decimal_places=2)

    class Meta:
        managed = False
        db_table = "mv_trial_balance_running"
        verbose_name = "Trial Balance Running"
        verbose_name_plural = "Trial Balance Running Records"
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "account_id"],
                name="ux_mv_trial_balance_running_company_account",
            ),
        ]


""" Profit & Loss (period-based) """


class ProfitLossPeriod(models.Model):
    company_id = models.IntegerField()
    period_id = models.IntegerField()
    total_income_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    total_expense_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    net_profit_original = models.DecimalField(max_digits=18, decimal_places=2)
    total_income_local = models.DecimalField(max_digits=18, decimal_places=2)
    total_expense_local = models.DecimalField(max_digits=18, decimal_places=2)
    net_profit_local = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        managed = False
        db_table = "mv_pl_period"
        verbose_name = "Profit & Loss Period"
        verbose_name_plural = "Profit & Loss Period Records"
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "period_id"],
                name="ux_mv_pl_period_company_period",
            ),
        ]


""" Balance Sheet (snapshot) """


class BalanceSheetRunning(models.Model):
    company_id = models.IntegerField()
    account_id = models.IntegerField()
    account_code = models.CharField(max_length=50)
    account_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50)
    balance_to_date_original = models.DecimalField(
        max_digits=18, decimal_places=2)
    balance_to_date_local = models.DecimalField(
        max_digits=18, decimal_places=2)

    class Meta:
        managed = False
        db_table = "mv_balance_sheet_running"
        verbose_name = "Balance Sheet Running"
        verbose_name_plural = "Balance Sheet Running Records"
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "account_id"],
                name="ux_mv_balance_sheet_running_company_account",
            ),
        ]
