from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0016_replace_mv_trial_balance_period"),
    ]

    """ Running Trial Balance Up to date:
        This materialized view gives you the running trial balance:
        - One row per account per company.
        - Shows total debits, total credits, and net balance from 
          the beginning of time up to now.
        - Unlike the period trial balance, this doesn't reset each month/period.
    """

    operations = [
        migrations.RunSQL(
            """  
            DROP MATERIALIZED VIEW IF EXISTS mv_trial_balance_running CASCADE;
            CREATE MATERIALIZED VIEW mv_trial_balance_running AS

            -- `WITH jl_norm` is above everything, so it can be reused in final query
            WITH jl_norm AS (
                SELECT
                    md5(jl.company_id::text || '-' || COALESCE(jl.account_id::text, '')) AS tb_id,
                    jl.company_id,
                    jl.account_id,
                    COALESCE(jl.fx_rate, 1.0) AS fxr,
                    jl.debit_original,
                    jl.credit_original
                FROM accounts_core_journalline jl
                JOIN accounts_core_journalentry je 
                    ON je.id = jl.journal_id
                -- filter early
                WHERE je.status = 'posted' 
            )
            -- only one final SELECT (Postgres requires this)
            SELECT
                jl_norm.tb_id AS id,               -- use the non-ambiguous alias here
                jl_norm.company_id,
                a.id   AS account_id,
                a.code AS account_code,
                a.name AS account_name,
                a.ac_type AS account_type,
                
                -- Original & local totals in the same projection
                -- Original currency amounts
                SUM(jl_norm.debit_original)  AS total_debit_to_date_original,
                SUM(jl_norm.credit_original) AS total_credit_to_date_original,
                SUM(jl_norm.debit_original) - SUM(jl_norm.credit_original) AS balance_to_date_original,

                -- Local currency amounts
                SUM(jl_norm.debit_original * jl_norm.fxr)  AS total_debit_to_date_local,
                SUM(jl_norm.credit_original * jl_norm.fxr) AS total_credit_to_date_local,
                SUM(jl_norm.debit_original * jl_norm.fxr) 
                - SUM(jl_norm.credit_original * jl_norm.fxr) AS balance_to_date_local

            FROM jl_norm
            JOIN accounts_core_account a ON a.id = jl_norm.account_id
            GROUP BY jl_norm.tb_id, jl_norm.company_id, a.id, a.code, a.name, a.ac_type;

            CREATE UNIQUE INDEX ux_mv_trial_balance_running_company_account
                ON mv_trial_balance_running (company_id, account_id);
            """,
            reverse_sql="DROP MATERIALIZED VIEW mv_trial_balance_running;",
        ),
    ]

    """ What version 0017 fixes
        - Added Explicit synthetic primary key: 1 row per (company, account), Deterministic primary key
        - Explicit column selection: No ambiguity
        - Corrected GROUP BY: 
            - PostgreSQL requires all non-aggregated columns
            - Ensures exactly one row per account per company
            - Matches accounting semantics of a running trial balance
        - Accounting correctness check in `balance_to_date`: structurally safer and framework-correct.
    """
