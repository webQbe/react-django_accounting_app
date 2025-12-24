from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0009_mv_trial_balance_running"),
        ("accounts_core", "0014_mv_balance_sheet_running"),
    ]

    """ Base aggregation matview :
        - This view answers: 
         For each company + period + account, what is the aggregated balance from posted journal entries?
    """

    operations = [
        migrations.RunSQL(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_jl_agg_period CASCADE;
            CREATE MATERIALIZED VIEW mv_jl_agg_period AS
            WITH jl_norm AS (
                -- pick explicit columns we need from journalline + fx
                SELECT
                    jl.company_id,
                    jl.account_id,
                    COALESCE(jl.fx_rate, 1.0) AS fxr,
                    jl.debit_original,
                    jl.credit_original,
                    je.period_id,
                    je.date::date AS txn_date,
                    a.code AS account_code,
                    a.name AS account_name,
                    a.ac_type AS account_type
                FROM accounts_core_journalline jl
                JOIN accounts_core_journalentry je ON je.id = jl.journal_id
                JOIN accounts_core_account a ON a.id = jl.account_id
                WHERE je.status = 'posted'
            ),
            grouped AS (
                -- aggregate first (one row per company/period/account)
                SELECT
                    company_id,
                    period_id,
                    account_id,
                    MAX(txn_date) AS last_txn_date,
                    account_code,
                    account_name,
                    account_type,
                    SUM(debit_original)  AS total_debit_original,
                    SUM(credit_original) AS total_credit_original,
                    SUM(debit_original) - SUM(credit_original) AS net_amount_original,
                    SUM(debit_original * fxr)  AS total_debit_local,
                    SUM(credit_original * fxr) AS total_credit_local,
                    SUM(debit_original * fxr) - SUM(credit_original * fxr) AS net_amount_local
                FROM jl_norm
                GROUP BY company_id, period_id, account_id, account_code, account_name, account_type
            )
            SELECT
                md5(company_id::text || '-' || COALESCE(period_id::text, '') || '-' || account_id::text) AS id,
                company_id,
                period_id,
                last_txn_date,
                account_id,
                account_code,
                account_name,
                account_type,
                total_debit_original,
                total_credit_original,
                net_amount_original,
                total_debit_local,
                total_credit_local,
                net_amount_local
            FROM grouped;

            -- add indexes
            CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_jl_agg_period_company_period_account
                ON mv_jl_agg_period (company_id, period_id, account_id);

            CREATE INDEX IF NOT EXISTS ix_mv_jl_agg_period_company_account
                ON mv_jl_agg_period (company_id, account_id);
            """,
            # SQL to undo view if you roll back migration (dropping the view)
            reverse_sql="DROP MATERIALIZED VIEW mv_jl_agg_period;",
        ),
    ]


""" Version 0015_replace_update_mv_jl_agg_period
    1. Stable primary key: Deterministic, Unique and Matches Django `primary_key=True`
    2. Correct accounting grain: 1 row = 1 company × 1 period × 1 account
    3. Separation of concerns: 
       - jl_norm → normalize data, 
       - grouped → aggregate 
       - final SELECT → shape for Django
    4. Fix for admin error MultipleObjectsReturned:  One row per company + period + account, Stable, deterministic ID
        ⚠️ Earlier bug:
            - Grouped by date
            - That created multiple rows per account/period
            - Same id → admin exploded
    5. Indexes align perfectly: `UNIQUE (company_id, period_id, account_id)`
"""
