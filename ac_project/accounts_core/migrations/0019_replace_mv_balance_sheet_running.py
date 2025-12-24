from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0018_replace_mv_pl_period"),
    ]

    """  Balance Sheet (snapshot):
    This materialized view is a running Balance Sheet snapshot:
       -  Shows each company's Asset, Liability, and Equity accounts.
       -  Tracks their balances up to date.
       -  Enforces uniqueness and query speed with an index.
    """

    operations = [
        migrations.RunSQL(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_balance_sheet_running CASCADE;
            CREATE MATERIALIZED VIEW mv_balance_sheet_running AS
            WITH jl_norm AS (
            SELECT
                md5(jl.company_id::text || '-' || a.id::text) AS agg_id,
                jl.company_id,
                jl.account_id,
                COALESCE(jl.fx_rate, 1.0) AS fxr,
                jl.debit_original,
                jl.credit_original,
                a.code AS account_code,
                a.name AS account_name,
                a.ac_type
            FROM accounts_core_journalline jl
            JOIN accounts_core_journalentry je ON je.id = jl.journal_id
            JOIN accounts_core_account a ON a.id = jl.account_id
            WHERE je.status = 'posted'
                AND a.ac_type IN ('asset','liability','equity')
            )
            SELECT
                jl_norm.agg_id AS id,
                jl_norm.company_id,
                jl_norm.account_id,
                jl_norm.account_code,
                jl_norm.account_name,
                jl_norm.ac_type AS account_type,
                SUM(jl_norm.debit_original) - SUM(jl_norm.credit_original) AS balance_to_date_original,
                SUM(jl_norm.debit_original * jl_norm.fxr) - SUM(jl_norm.credit_original * jl_norm.fxr) AS balance_to_date_local
            FROM jl_norm
            GROUP BY jl_norm.agg_id, jl_norm.company_id, jl_norm.account_id, jl_norm.account_code, jl_norm.account_name, jl_norm.ac_type;

            CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_balance_sheet_running_company_account
                ON mv_balance_sheet_running (company_id, account_id);
            """,
            reverse_sql="DROP MATERIALIZED VIEW mv_balance_sheet_running;",
        ),
    ]

    """ In version 0019: 
        `FROM accounts_core_journalline jl
            WHERE ac_type IN ('asset','liability','equity')
        `
        - Self-contained
        - Correct accounting equation
        - FX applied consistently
        - Deterministic id
        - No cascading refresh bugs
    
        Why version 0014 Failed?
            - Chained dependency (`FROM mv_trial_balance_running`)
            - If trial balance breaks → balance sheet breaks
            - No id
    """
