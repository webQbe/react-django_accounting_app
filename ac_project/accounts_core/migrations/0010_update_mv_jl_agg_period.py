from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0009_mv_trial_balance_running"),
    ]

    """ Base aggregation matview :
        - Base, fine-grained aggregation by company/period/account. 
        - This materialized view creates a fast lookup table of account balances 
        per company and period, only using posted journal entries. 
        - Instead of recalculating debits/credits every time, the database stores them 
        ready to query. 
    """

    operations = [
        migrations.RunSQL(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_jl_agg_period CASCADE;
            CREATE MATERIALIZED VIEW mv_jl_agg_period AS
            
            -- `WITH jl_norm` is above everything, so it can be reused in final query
            WITH jl_norm AS (
                SELECT
                    jl.*,
                    je.period_id,
                    je.date::date AS txn_date,
                    COALESCE(jl.fx_rate, 1.0) AS fxr
                FROM accounts_core_journalline jl
                JOIN accounts_core_journalentry je 
                    ON je.id = jl.journal_id
                -- filter early
                WHERE je.status = 'posted' 
            )

            -- only one final SELECT (Postgres requires this)
            SELECT
                jl_norm.company_id,
                jl_norm.period_id,
                MAX(jl_norm.txn_date) AS last_txn_date,   -- *last* date, aggregate
                a.id AS account_id,
                a.code AS account_code, 
                a.name AS account_name,
                a.ac_type AS account_type,

                -- Original & local totals in the same projection
                -- Original currency amounts
                SUM(jl_norm.debit_original)  AS total_debit_original,
                SUM(jl_norm.credit_original) AS total_credit_original,
                SUM(jl_norm.debit_original) - SUM(jl_norm.credit_original) AS net_amount_original,
                
                -- Local currency amounts
                SUM(jl_norm.debit_original * jl_norm.fxr)  AS total_debit_local,
                SUM(jl_norm.credit_original * jl_norm.fxr) AS total_credit_local,
                SUM(jl_norm.debit_original * jl_norm.fxr) 
                - SUM(jl_norm.credit_original * jl_norm.fxr) AS net_amount_local

            FROM jl_norm
            JOIN accounts_core_account a ON a.id = jl_norm.account_id
            GROUP BY jl_norm.company_id, jl_norm.period_id, a.id, a.code, a.name, a.ac_type;
            
            CREATE UNIQUE INDEX ux_mv_jl_agg_period_company_period_account
                ON mv_jl_agg_period (company_id, period_id, account_id);

            CREATE INDEX ix_mv_jl_agg_period_company_account
                ON mv_jl_agg_period (company_id, account_id);
            """,
            # SQL to undo view if you roll back migration (dropping the view)
            reverse_sql="",
        ),
    ]

    """ Version 0010_update_mv_jl_agg_period (WITH + fx_rate)
       - Improvements over 0008
        - Correct aggregation grain: One row per (company, period, account)
        - MAX(txn_date) instead of grouping by date
        - FX normalization (fx_rate)
        - Cleaner structure     
    """
