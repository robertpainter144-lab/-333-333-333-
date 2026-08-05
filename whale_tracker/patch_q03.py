"""PATCH Q03 with the current (labels-free) SQL, using cached wallet list."""
import bootstrap

env    = bootstrap.load_env()
api    = env["DUNE_API_KEY"]
q02_id = int(env["DUNE_Q02_ID"])
q03_id = int(env["DUNE_Q03_ID"])

sql = open("03_live_whale_feed.sql").read()

# Reuse cached wallets from Q02's most recent completed execution
rows02  = bootstrap.execute_and_fetch(api, q02_id)
wallets = [r["wallet"] for r in rows02 if r.get("wallet")]
print(f"Pulled {len(wallets)} wallets from Q02")

spliced = bootstrap.splice_wallets(sql, wallets)
bootstrap.update_query_sql(api, q03_id, spliced)
print(f"Q03 (id={q03_id}) PATCHed with fresh SQL")
