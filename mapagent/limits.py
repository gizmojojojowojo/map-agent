"""Request limits that keep a public deployment's model bill bounded.

Two limits apply to each question:
- per client: at most `per_client` questions in any rolling hour, so one visitor cannot use up the day
- per day: at most `per_day` questions in total (UTC day), which caps spend however many visitors arrive

Counts live in memory, so they assume one server process and reset when it restarts.
Set a monthly spend limit in the Anthropic console as the hard backstop.
"""
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone


class Limited(Exception):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = int(retry_after)


class Limiter:
    def __init__(self, per_client=20, per_day=300, clock=time.time):
        self.per_client, self.per_day, self.clock = per_client, per_day, clock
        self.recent = defaultdict(deque)
        self.day, self.today = None, 0
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(per_client=int(os.environ.get("MAP_AGENT_LIMIT_PER_HOUR", 20)),
                   per_day=int(os.environ.get("MAP_AGENT_LIMIT_PER_DAY", 300)))

    def check(self, client):
        """Count one question from `client`, or raise Limited without counting it."""
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).date()
        with self.lock:
            if day != self.day:
                self.day, self.today = day, 0
            if self.today >= self.per_day:
                tomorrow = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + 86400
                raise Limited("This demo has answered its questions for today. Try again tomorrow.", tomorrow - now)
            times = self.recent[client]
            while times and times[0] <= now - 3600:
                times.popleft()
            if len(times) >= self.per_client:
                raise Limited(f"You've asked {self.per_client} questions in the last hour. "
                              "Try again a little later.", times[0] + 3600 - now)
            times.append(now)
            self.today += 1
            if len(self.recent) > 10_000:  # drop idle clients so memory stays bounded
                for key in [k for k, v in self.recent.items() if not v or v[-1] <= now - 3600]:
                    del self.recent[key]

    def refund(self, client):
        """Uncount the client's latest question, for one the server failed to answer."""
        with self.lock:
            if self.recent.get(client):
                self.recent[client].pop()
                self.today = max(0, self.today - 1)
