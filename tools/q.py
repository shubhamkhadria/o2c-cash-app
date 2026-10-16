import sqlite3
import sys

import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

sql = open(sys.argv[1]).read()
con = sqlite3.connect("o2c.db")
print(pd.read_sql(sql, con).to_string(index=False))
con.close()
