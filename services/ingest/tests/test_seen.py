"""Seen set: file persistence plus the Redis mirror that survives deploys."""

from datetime import datetime, timedelta, timezone

import pytest

import seen


class FakeRedis:
    """The slice of redis-py the mirror uses: hashes + a pipeline."""

    def __init__(self, fail: bool = False):
        self.hashes: dict[str, dict[str, str]] = {}
        self.fail = fail
        self.writes = 0

    def hgetall(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return dict(self.hashes.get(key, {}))

    def pipeline(self):
        return _Pipe(self)


class _Pipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def hset(self, key, mapping):
        self.ops.append(("hset", key, mapping))

    def hdel(self, key, *fields):
        self.ops.append(("hdel", key, fields))

    def execute(self):
        if self.r.fail:
            raise ConnectionError("redis down")
        for op, key, arg in self.ops:
            h = self.r.hashes.setdefault(key, {})
            if op == "hset":
                h.update(arg)
                self.r.writes += len(arg)
            else:
                for f in arg:
                    h.pop(f, None)


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(seen, "_client", r)
    monkeypatch.setattr(seen, "_client_failed", False)
    monkeypatch.setattr(seen, "_down_until", 0.0)
    monkeypatch.setattr(seen, "_mirrored", {})
    return r


def _now(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_deploy_with_empty_disk_restores_from_redis(fake, tmp_path):
    path = str(tmp_path / "seen.json")
    seen.save_seen({"a": _now(), "b": _now()}, path)

    # New container: no file on disk, fresh process state
    (tmp_path / "seen.json").unlink()
    seen._mirrored.clear()
    assert seen.load_seen(path) == fake.hashes["ingest:seen:seen.json"]
    assert set(seen.load_seen(path)) == {"a", "b"}


def test_only_the_diff_is_written(fake, tmp_path):
    path = str(tmp_path / "seen.json")
    state = {f"id{i}": _now() for i in range(50)}
    seen.save_seen(state, path)
    assert fake.writes == 50

    state["new"] = _now()
    seen.save_seen(state, path)
    assert fake.writes == 51          # one entry, not the whole set again


def test_pruned_entries_leave_redis_too(fake, tmp_path):
    path = str(tmp_path / "seen.json")
    seen.save_seen({"old": _now(), "fresh": _now()}, path)
    seen.save_seen({"old": _now(days_ago=seen.TTL_DAYS + 1), "fresh": _now()}, path)
    assert set(fake.hashes["ingest:seen:seen.json"]) == {"fresh"}


def test_each_file_gets_its_own_hash(fake, tmp_path):
    seen.save_seen({"edgar-1": _now()}, str(tmp_path / "seen.json"))
    seen.save_seen({"fmp:x": _now()}, str(tmp_path / "pr_seen.json"))
    assert set(fake.hashes) == {"ingest:seen:seen.json", "ingest:seen:pr_seen.json"}


def test_redis_outage_never_breaks_ingest(fake, tmp_path):
    path = str(tmp_path / "seen.json")
    fake.fail = True
    seen.save_seen({"a": _now()}, path)          # file still written
    assert seen.load_seen(path) == {"a": seen.load_seen(path)["a"]}

    # Back up after the stand-down: the missed diff is written then
    fake.fail = False
    seen._down_until = 0.0
    seen.save_seen({"a": seen.load_seen(path)["a"]}, path)
    assert "a" in fake.hashes["ingest:seen:seen.json"]


def test_no_redis_url_means_file_only(monkeypatch, tmp_path):
    monkeypatch.setattr(seen, "_client", None)
    monkeypatch.setattr(seen, "_client_failed", False)
    monkeypatch.setattr(seen, "_down_until", 0.0)
    monkeypatch.delenv("REDIS_URL", raising=False)
    path = str(tmp_path / "seen.json")
    seen.save_seen({"a": _now()}, path)
    assert set(seen.load_seen(path)) == {"a"}
