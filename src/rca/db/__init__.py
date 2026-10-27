"""Postgres connection helpers.

One database holds warehouse, ops, lineage and knowledge (DESIGN §4).
"""

import psycopg
from psycopg.rows import DictRow, dict_row

Connection = psycopg.Connection[DictRow]


def connect(url: str, *, autocommit: bool = False) -> Connection:
    return psycopg.connect(url, autocommit=autocommit, row_factory=dict_row)
