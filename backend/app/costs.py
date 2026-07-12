"""AIジョブのコスト実算定（#25 — 張りぼて cost_usd=0.0 の置換）。

単価は config.py の単価テーブル（USD / 100万トークン。環境変数で上書き可）。
モデルの割当は「ジョブ kind から導出」で確定する（gemini_provider.py と同じ規約）:

    execute                              → Pro 単価（gemini-2.5-pro）
    review / orchestrate / breakdown / distill → Flash 単価（gemini-2.5-flash）

成功確定（ai_jobs_repo.mark_succeeded）を行う全箇所 —
execute.py（ステップ2 / _handoff_plan）・review.py::_mark_succeeded・
orchestrate.py::_mark_succeeded — が必ず本関数を通ること（#24 引き継ぎ事項）。
mock プロバイダでも同じ式で算定する（デモでも $ が動く）。
DB 側は numeric(10,4) なので保存時に4桁へ丸まる（表示・上限判定は DB 値が真実）。
"""

from app.ai.provider import TokenUsage
from app.config import get_settings
from app.domain.models import AiJobKind

# 単価テーブルの分母（USD / 100万トークン）
_TOKENS_PER_PRICE_UNIT = 1_000_000


def calc_cost_usd(kind: AiJobKind | str, usage: TokenUsage) -> float:
    """ジョブ kind とトークン使用量から概算コスト（USD）を返す。

    usage はストリーム経路でも「最終チャンク累計」（provider が返す唯一の真実）を
    渡すこと。チャンク積算は二重計上になる（#24 引き継ぎ事項）。
    """
    settings = get_settings()
    if AiJobKind(kind) is AiJobKind.EXECUTE:
        input_price = settings.price_pro_input_usd_per_mtok
        output_price = settings.price_pro_output_usd_per_mtok
    else:
        input_price = settings.price_flash_input_usd_per_mtok
        output_price = settings.price_flash_output_usd_per_mtok
    return (
        usage.input_tokens * input_price + usage.output_tokens * output_price
    ) / _TOKENS_PER_PRICE_UNIT
