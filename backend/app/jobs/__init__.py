"""非同期ジョブ層（§7.2 実行基盤）。

- queue.py: ジョブランナー抽象（JOB_RUNNER=local|cloud_tasks）
- registry.py: kind → 実行関数のディスパッチテーブル（#18。新 kind の登録はここ）
- execute.py: 実作業（execute）ジョブ本体（§7.3）
"""
