import sqlite3, sys
import pandas as pd

print("Python", sys.version.split()[0], "| pandas", pd.__version__)

con = sqlite3.connect("o2c.db")
con.execute("""CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        name        TEXT,
        region      TEXT)""")
con.execute("INSERT OR REPLACE INTO customers VALUES ('C1001', 'Acme Corp', 'Midwest')")
con.commit()

print(pd.read_sql("SELECT * FROM customers", con))
con.close()