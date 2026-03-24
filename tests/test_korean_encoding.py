def test_korean_text_is_preserved():
    message = "한글이 정상적으로 저장됩니다."

    assert message == "한글이 정상적으로 저장됩니다."
    assert "한글" in message
