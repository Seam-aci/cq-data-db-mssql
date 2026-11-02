import os
import socket
import struct
import sys
import time

import pandas as pd
import pymssql


def default_gateway():
    with open("/proc/net/route") as fh:
        for line in fh.readlines()[1:]:
            fields = line.strip().split()
            if fields[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return None


MSSQL_HOST = os.environ["MSSQL_HOST"]
if MSSQL_HOST == "auto-gateway":
    MSSQL_HOST = default_gateway()
    print(f"Resolved auto-gateway MSSQL_HOST to {MSSQL_HOST}")
MSSQL_SA_PASSWORD = os.environ["MSSQL_SA_PASSWORD"]
MSSQL_DATABASE = os.environ.get("MSSQL_DATABASE", "ChequeDB")
EXCEL_FILE = os.environ.get("EXCEL_FILE", "/data/source.xlsx")
TRUNCATE_BEFORE_LOAD = os.environ.get("TRUNCATE_BEFORE_LOAD", "true").lower() == "true"

COLUMNS = [
    ("business", "Business", "NVARCHAR(50)"),
    ("customer_code", "Customer Code", "NVARCHAR(20)"),
    ("sub_business", "Sub-business", "NVARCHAR(50)"),
    ("credit_limit", "Credit Limit", "DECIMAL(18,2)"),
    ("credit_days", "Credit Days", "INT"),
    ("due", "Due", "DECIMAL(18,2)"),
    ("od", "OD", "DECIMAL(18,2)"),
    ("age", "Age", "INT"),
]

TABLE_NAME = "cheque_data"


def connect(database="master", autocommit=True):
    return pymssql.connect(
        server=MSSQL_HOST,
        user="sa",
        password=MSSQL_SA_PASSWORD,
        database=database,
        autocommit=autocommit,
        login_timeout=10,
    )


def wait_for_server(retries=30, delay=5):
    for attempt in range(1, retries + 1):
        try:
            conn = connect()
            conn.close()
            print("Connected to MSSQL server.")
            return
        except Exception as exc:
            print(f"[{attempt}/{retries}] MSSQL not ready yet: {exc}")
            time.sleep(delay)
    print("Could not connect to MSSQL server, giving up.", file=sys.stderr)
    sys.exit(1)


def ensure_database():
    conn = connect(database="master")
    cur = conn.cursor()
    cur.execute(
        f"IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = '{MSSQL_DATABASE}') "
        f"CREATE DATABASE [{MSSQL_DATABASE}]"
    )
    conn.close()


def ensure_table():
    conn = connect(database=MSSQL_DATABASE)
    cur = conn.cursor()
    col_defs = ",\n            ".join(f"[{col}] {sqltype}" for col, _, sqltype in COLUMNS)
    cur.execute(
        f"""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = '{TABLE_NAME}')
        CREATE TABLE [{TABLE_NAME}] (
            id INT IDENTITY(1,1) PRIMARY KEY,
            {col_defs}
        )
        """
    )
    if TRUNCATE_BEFORE_LOAD:
        cur.execute(f"TRUNCATE TABLE [{TABLE_NAME}]")
    conn.close()


def load_excel():
    print(f"Reading Excel file: {EXCEL_FILE}")
    df = pd.read_excel(EXCEL_FILE, sheet_name=0)
    excel_headers = [h for _, h, _ in COLUMNS]
    df = df[excel_headers]
    df = df.where(pd.notnull(df), None)
    return df


def insert_data(df):
    conn = connect(database=MSSQL_DATABASE, autocommit=False)
    cur = conn.cursor()

    col_names = ", ".join(f"[{col}]" for col, _, _ in COLUMNS)
    placeholders = ", ".join("%s" for _ in COLUMNS)
    insert_sql = f"INSERT INTO [{TABLE_NAME}] ({col_names}) VALUES ({placeholders})"

    rows = list(df.itertuples(index=False, name=None))
    batch_size = 1000
    total = len(rows)
    for i in range(0, total, batch_size):
        batch = rows[i : i + batch_size]
        cur.executemany(insert_sql, batch)
        conn.commit()
        print(f"Inserted {min(i + batch_size, total)}/{total} rows")

    conn.close()


def main():
    wait_for_server()
    ensure_database()
    ensure_table()
    df = load_excel()
    insert_data(df)
    print("Data load complete.")


if __name__ == "__main__":
    main()
