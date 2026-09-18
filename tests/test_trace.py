from campaignguard.trace import sanitize


def test_sanitize_redacts_secrets_drops_reasoning_and_truncates():
    raw = {"api_key": "sk-abc", "headers": {"Authorization": "Bearer xyz"},
           "items": [{"type": "reasoning", "summary": "hidden"}, {"type": "function_call", "arguments": "key sk-live1234567890abc"}],
           "note": "x" * 1000}
    out = sanitize(raw)
    assert "api_key" not in out and "headers" not in out
    assert out["items"] == [{"type": "function_call", "arguments": "key [REDACTED]"}]
    assert len(out["note"]) < 400
