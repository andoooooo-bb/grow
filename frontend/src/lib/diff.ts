// 行単位の LCS diff（#20 差分リプレイ）。依存追加なしの軽量自前実装。
// 版間の Markdown を行で比較し、同一=equal / 追加=add / 削除=del の並びを返す。
// ArtifactSection の差分ビュー（追加=緑背景 / 削除=赤背景・取り消し線）が使う。

export type DiffOp = 'equal' | 'add' | 'del';

export interface DiffLine {
  op: DiffOp;
  text: string;
}

/** 空文字は 0 行として扱う（''.split('\n') が [''] になるのを防ぐ） */
function toLines(text: string): string[] {
  return text === '' ? [] : text.split('\n');
}

/**
 * before → after の行 diff を返す（LCS 動的計画法 O(n·m)。成果物は高々数百行）。
 * 同一 hunk 内では del を add より先に出す（一般的な diff 表示順）。
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = toLines(before);
  const b = toLines(after);
  const n = a.length;
  const m = b.length;

  // dp[i][j] = a[i:] と b[j:] の LCS 長（後ろから埋める）
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  // 先頭から辿って equal / del / add を発行する
  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      result.push({ op: 'equal', text: a[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ op: 'del', text: a[i] });
      i += 1;
    } else {
      result.push({ op: 'add', text: b[j] });
      j += 1;
    }
  }
  while (i < n) {
    result.push({ op: 'del', text: a[i] });
    i += 1;
  }
  while (j < m) {
    result.push({ op: 'add', text: b[j] });
    j += 1;
  }
  return result;
}
