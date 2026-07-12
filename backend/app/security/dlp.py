"""機微情報スキャナ（#29 §6.7: 固有名詞・秘密情報をルール文に焼き込まない）。

個人ルールをチームへ昇格（形式知化）する前に、ルール文へ機微情報
（人名・メールアドレス・電話番号・マイナンバー・クレジットカード番号）が
含まれていないかを検査するガードレール。

切替は AI_PROVIDER と同じ1変数（00 §0.1 のプロバイダ切替に相乗り）:
- gemini（本番）: Google Cloud Sensitive Data Protection（Cloud DLP）の
  inspect_content を呼ぶ。クライアントは gemini_provider と同様の遅延初期化
  （初回呼び出し時に生成。認証不要のローカルテストを可能に）。
- mock（ローカル・テスト）: 正規表現ベースの決定的スタブ。ネットワーク不要・
  費用ゼロ・同じ入力には常に同じ出力。

検出結果はどちらの実装でも Finding(info_type, quote) のリストに正規化する。
"""

import re
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

# 検査対象の infoType（Cloud DLP の組み込み infoType 名。mock スタブも同名を使う）
INSPECT_INFO_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "JAPAN_INDIVIDUAL_NUMBER",
    "CREDIT_CARD_NUMBER",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """検出1件（info_type: DLP infoType 名 / quote: 該当文字列）。"""

    info_type: str
    quote: str


async def inspect_rule_text(text: str) -> list[Finding]:
    """ルール文の機微情報を検査する（検出なしなら空リスト）。

    AI_PROVIDER=gemini なら Cloud DLP、mock なら正規表現スタブ。
    呼び出し側（routers/rules.py の promote / generalize）は実装を意識しない。
    """
    if get_settings().ai_provider == "gemini":
        return await inspect_with_cloud_dlp(text)
    return inspect_with_regex(text)


# ---- 本番: Cloud DLP（Sensitive Data Protection） ----------------------------------

# 遅延初期化クライアント（gemini_provider._get_client と同じ方針。
# google-cloud-dlp は import が重いので関数内で遅延 import する）
_dlp_client: Any = None


def _get_dlp_client() -> Any:
    global _dlp_client
    if _dlp_client is None:
        from google.cloud import dlp_v2

        _dlp_client = dlp_v2.DlpServiceAsyncClient()
    return _dlp_client


async def inspect_with_cloud_dlp(text: str) -> list[Finding]:
    """Cloud DLP inspect_content でルール文を検査する（include_quote で該当文字列も取得）。"""
    settings = get_settings()
    client = _get_dlp_client()
    response = await client.inspect_content(
        request={
            "parent": f"projects/{settings.gcp_project}",
            "inspect_config": {
                "info_types": [{"name": name} for name in INSPECT_INFO_TYPES],
                "include_quote": True,
            },
            "item": {"value": text},
        }
    )
    findings: list[Finding] = []
    for finding in response.result.findings:
        info_type = getattr(finding.info_type, "name", "") or ""
        findings.append(Finding(info_type=info_type, quote=finding.quote or ""))
    return findings


# ---- ローカル/テスト: 正規表現スタブ（決定的） --------------------------------------

# infoType ごとの簡易パターン。日本人名は「様/さん」敬称の直前の漢字2〜4字という
# デモ向けの決定的ヒューリスティック（例「田中様」→「田中」を PERSON_NAME 検出）。
_STUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PERSON_NAME", re.compile(r"[一-鿿]{2,4}(?=様|さん)")),
    ("EMAIL_ADDRESS", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PHONE_NUMBER", re.compile(r"0\d{1,4}-\d{1,4}-\d{3,4}")),
    ("JAPAN_INDIVIDUAL_NUMBER", re.compile(r"\b\d{4}-\d{4}-\d{4}\b(?!-)")),
    ("CREDIT_CARD_NUMBER", re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b")),
)


def inspect_with_regex(text: str) -> list[Finding]:
    """正規表現スタブ検査（mock）。出現位置順で安定に返す（決定的）。

    マイナンバー簡易パターンはクレジットカード番号の一部（後半3群）にも一致するため、
    既に採用した検出スパンと重なる検出は捨てる（span 重なりの貪欲抑制）。長い/先に
    始まるスパンを優先することで、CC 番号は CREDIT_CARD_NUMBER 1件に正規化される。
    """
    # (start, end, order, finding) を全パターンから収集
    hits: list[tuple[int, int, int, Finding]] = []
    for order, (info_type, pattern) in enumerate(_STUB_PATTERNS):
        for match in pattern.finditer(text):
            hits.append(
                (
                    match.start(),
                    match.end(),
                    order,
                    Finding(info_type=info_type, quote=match.group()),
                )
            )
    # 開始位置昇順 → スパン長降順 → infoType 定義順。重なるスパンは先勝ちで抑制する
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0]), h[2]))
    result: list[Finding] = []
    occupied: list[tuple[int, int]] = []
    for start, end, _order, finding in hits:
        if any(start < occ_end and end > occ_start for occ_start, occ_end in occupied):
            continue
        occupied.append((start, end))
        result.append(finding)
    return result
