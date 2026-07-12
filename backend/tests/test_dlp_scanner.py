"""機微情報スキャナ（#29 app/security/dlp.py）の mock スタブ単体テスト。

AI_PROVIDER=mock（既定）時の正規表現スタブが決定的に検出/通過することを確認する。
実 GCP（Cloud DLP）は呼ばない。
"""

import pytest

from app.security.dlp import (
    Finding,
    inspect_rule_text,
    inspect_with_regex,
)


def _types(findings: list[Finding]) -> list[str]:
    return [f.info_type for f in findings]


def test_detects_email() -> None:
    findings = inspect_with_regex("連絡は hiroki.ando@example.co.jp まで")
    assert _types(findings) == ["EMAIL_ADDRESS"]
    assert findings[0].quote == "hiroki.ando@example.co.jp"


def test_detects_phone() -> None:
    findings = inspect_with_regex("電話は 03-1234-5678 に")
    assert _types(findings) == ["PHONE_NUMBER"]
    assert findings[0].quote == "03-1234-5678"


def test_detects_person_name_with_honorific() -> None:
    """「様/さん」の直前の漢字2〜4字を PERSON_NAME として検出する。"""
    findings = inspect_with_regex("田中様に確認する。佐藤さんへも共有")
    assert _types(findings) == ["PERSON_NAME", "PERSON_NAME"]
    assert [f.quote for f in findings] == ["田中", "佐藤"]


def test_detects_credit_card_and_my_number() -> None:
    cc = inspect_with_regex("カード 4111-1111-1111-1111 を登録")
    assert _types(cc) == ["CREDIT_CARD_NUMBER"]
    assert cc[0].quote == "4111-1111-1111-1111"

    mn = inspect_with_regex("マイナンバー 1234-5678-9012 を控える")
    assert _types(mn) == ["JAPAN_INDIVIDUAL_NUMBER"]
    assert mn[0].quote == "1234-5678-9012"


def test_multiple_findings_sorted_by_position() -> None:
    """複数種別が混在しても出現位置順で安定に返す。"""
    findings = inspect_with_regex("田中様のメール a@b.co に 03-1234-5678 で連絡")
    assert _types(findings) == ["PERSON_NAME", "EMAIL_ADDRESS", "PHONE_NUMBER"]


def test_clean_text_passes() -> None:
    """機微情報を含まない一般化されたルール文は空リスト（通過）。"""
    assert inspect_with_regex("レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く") == []
    assert inspect_with_regex("社外向け文書は敬体。数値は必ず出典を明記する") == []


async def test_inspect_rule_text_uses_mock_by_default() -> None:
    """既定（AI_PROVIDER=mock）では inspect_rule_text が正規表現スタブへ委譲する。"""
    findings = await inspect_rule_text("担当は 090-1111-2222")
    assert _types(findings) == ["PHONE_NUMBER"]


async def test_inspect_rule_text_gemini_calls_cloud_dlp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI_PROVIDER=gemini では Cloud DLP 経路を通る（クライアントはスタブ化して呼ばない）。"""
    from app import security
    from app.config import get_settings

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    get_settings.cache_clear()

    called: dict[str, str] = {}

    async def _fake_cloud_dlp(text: str) -> list[Finding]:
        called["text"] = text
        return [Finding(info_type="PERSON_NAME", quote="山田")]

    monkeypatch.setattr(security.dlp, "inspect_with_cloud_dlp", _fake_cloud_dlp)
    try:
        findings = await inspect_rule_text("山田様へ")
    finally:
        get_settings.cache_clear()

    assert called["text"] == "山田様へ"
    assert _types(findings) == ["PERSON_NAME"]
