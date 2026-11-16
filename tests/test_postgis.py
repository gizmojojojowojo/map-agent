"""PostGIS store parity with MemoryStore on the fixture.

Skipped unless MAP_AGENT_DATABASE_URL points at a database loaded with
scripts/load_data.py (the fixture, not real data).
"""
import json
import os

import pytest

from evals.run import QUESTIONS, expected_sites
from mapagent.store import MemoryStore

URL = os.environ.get("MAP_AGENT_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MAP_AGENT_DATABASE_URL not set")
QUESTIONS_WITH_CALLS = [q for q in json.loads(QUESTIONS.read_text()) if "calls" in q]


@pytest.fixture(scope="module")
def stores():
    from mapagent.store import PostgisStore
    postgis = PostgisStore(URL)
    if postgis.connection.execute("SELECT count(*) AS n FROM map_agent.sites").fetchone()["n"] != len(MemoryStore().sites):
        pytest.skip("database does not hold the fixture; run scripts/load_data.py")
    return MemoryStore(), postgis


@pytest.mark.parametrize("question", QUESTIONS_WITH_CALLS, ids=lambda q: q["id"])
def test_same_sites_as_memory_store(stores, question):
    memory, postgis = stores
    assert expected_sites(postgis, question["calls"]) == expected_sites(memory, question["calls"])


def test_store_is_read_only(stores):
    import psycopg
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        stores[1].connection.execute("DELETE FROM map_agent.sites")
