import httpx
import pytest

from app.tiktok import TikTokClient, TikTokError, chunk_ranges, plan_chunks
from app.tiktok.client import MAX_CHUNK, MIN_CHUNK


@pytest.mark.parametrize("size", [1, 4_000_000, MIN_CHUNK, MAX_CHUNK])
def test_small_files_go_in_one_chunk(size):
    assert plan_chunks(size, 10 * 1024 * 1024) == (size, 1)


@pytest.mark.parametrize("size", [MAX_CHUNK + 1, 65_000_000, 500_000_000, 4 * 1024**3])
@pytest.mark.parametrize("preferred", [1, 10 * 1024 * 1024, 200_000_000])
def test_large_files_follow_chunk_rules(size, preferred):
    chunk, total = plan_chunks(size, preferred)
    assert MIN_CHUNK <= chunk <= MAX_CHUNK
    assert total == size // chunk
    assert total <= 1000
    ranges = chunk_ranges(size, chunk, total)
    assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
    for (_, prev_end), (start, _) in zip(ranges, ranges[1:], strict=False):
        assert start == prev_end + 1
    last = ranges[-1][1] - ranges[-1][0] + 1
    assert chunk <= last < 128_000_000


def test_authorize_url_contains_required_params(tt):
    url = httpx.URL(tt.authorize_url("https://panel.example/cb", "st", "user.info.basic,video.publish"))
    params = dict(url.params)
    assert params["client_key"] == "test-client-key"
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == "https://panel.example/cb"
    assert params["state"] == "st"
    assert params["scope"] == "user.info.basic,video.publish"


def test_exchange_and_refresh(fake, tt):
    ts = tt.exchange_code(fake.issue_code("open-alpha"), "http://testserver/cb")
    assert ts.open_id == "open-alpha"
    assert "video.publish" in ts.scope
    refreshed = tt.refresh(ts.refresh_token)
    assert refreshed.access_token != ts.access_token


def test_oauth_error_is_not_transient(tt):
    with pytest.raises(TikTokError) as exc:
        tt.exchange_code("bad-code", "http://testserver/cb")
    assert exc.value.code == "invalid_grant"
    assert not exc.value.transient
    assert exc.value.is_auth_problem


def test_api_errors_are_classified(fake, tt):
    token = tt.exchange_code(fake.issue_code("open-alpha"), "x").access_token
    fake.fail(
        "/v2/post/publish/creator_info/query/",
        (429, "rate_limit_exceeded"),
        (403, "spam_risk_too_many_posts"),
        "network",
    )
    with pytest.raises(TikTokError) as e1:
        tt.creator_info(token)
    assert e1.value.transient
    with pytest.raises(TikTokError) as e2:
        tt.creator_info(token)
    assert not e2.value.transient and e2.value.code == "spam_risk_too_many_posts"
    with pytest.raises(TikTokError) as e3:
        tt.creator_info(token)
    assert e3.value.code == "network_error" and e3.value.transient
    assert tt.creator_info(token)["creator_username"] == "alpha"


def test_non_json_5xx_is_transient():
    client = TikTokClient(
        "k",
        "s",
        "http://api.test",
        "http://auth.test",
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="Bad gateway"))),
    )
    with pytest.raises(TikTokError) as exc:
        client.creator_info("t")
    assert exc.value.transient
