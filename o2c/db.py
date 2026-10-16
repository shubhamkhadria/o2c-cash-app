"""
o2c/db.py  -  Week 8: the one place that knows where the database is and how to read it.

Every page of the app imports these functions instead of opening its own connection.
If the database ever moves (a different file, or a real server), only this file changes.
"""

import sqlite3

import pandas as pd

DB_PATH = "o2c.db"


def query(sql, params=()):
    """Run a SELECT and return the result as a table (a pandas DataFrame)."""
    con = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql(sql, con, params=params)
    finally:
        con.close()                      # always close, even if the SQL has an error


def query_file(path):
    """Run a saved .sql file, e.g. your week 3 aging report."""
    with open(path) as f:
        return query(f.read())
