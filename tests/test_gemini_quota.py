from datetime import datetime, timedelta, timezone

from bayesly import net
from bayesly.gemini import GeminiClient, GeminiError, QuotaStore

DAILY = ('{"error":{"message":"Rate limit exceeded for model gemini-3.6-flash (limit: 20 requests per day on '
         'Free Tier). Please retry in 7h49m15s or upgrade your tier.","code":"too_many_requests"}}')


class Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text

    def json(self):
        return {"output_text": "ok"}


class Sess:
    def __init__(self, answers):
        self.answers, self.models = answers, []

    def request(self, method, url, **kw):
        m = kw["json"]["model"]
        self.models.append(m)
        code = self.answers.get(m, 200)
        return Resp(code, DAILY if code == 429 else "{}")


def test_daily_quota_is_remembered_and_skipped(tmp_path):
    s = Sess({"a": 429})
    c = GeminiClient("k", "a", "r", timeout=5, max_retries=2, session=s, fallback_models=["b"],
                     quota_path=tmp_path / "q.json", utility_models=["lite"])
    assert c.generate("hi") == "ok" and s.models == ["a", "b"]          # 429 per day: no retries, next model
    assert c.generate("hi") == "ok" and s.models[-1] == "b" and s.models.count("a") == 1   # 'a' skipped now
    until = datetime.fromisoformat(QuotaStore(tmp_path / "q.json")._load()["a"])
    assert timedelta(hours=7, minutes=49) < until - datetime.now(timezone.utc) < timedelta(hours=8)
    assert c.available()
    s.answers["b"] = 429
    try:
        c.generate("hi")
    except GeminiError:
        pass
    assert not c.available()
    assert c.utility().generate("x") == "ok" and s.models[-1] == "lite"   # small tasks use the lite pool


def test_net_fails_fast_on_daily_quota():
    class S:
        calls = 0

        def request(self, *a, **k):
            S.calls += 1
            return Resp(429, DAILY)
    try:
        net.request("POST", "u", timeout=1, max_retries=3, session=S())
    except net.ExternalCallError as exc:
        assert "per day" in str(exc)
    assert S.calls == 1
