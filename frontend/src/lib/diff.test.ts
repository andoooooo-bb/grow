import { describe, expect, it } from 'vitest';
import { diffLines } from './diff.ts';

describe('diffLines（#20 行単位 LCS diff）', () => {
  it('同一テキストは全行 equal', () => {
    const text = '# 見出し\n\n本文';
    expect(diffLines(text, text)).toEqual([
      { op: 'equal', text: '# 見出し' },
      { op: 'equal', text: '' },
      { op: 'equal', text: '本文' },
    ]);
  });

  it('行の追加は add として末尾・途中どちらでも検出する', () => {
    expect(diffLines('a\nc', 'a\nb\nc\nd')).toEqual([
      { op: 'equal', text: 'a' },
      { op: 'add', text: 'b' },
      { op: 'equal', text: 'c' },
      { op: 'add', text: 'd' },
    ]);
  });

  it('行の削除は del として検出する', () => {
    expect(diffLines('a\nb\nc', 'a\nc')).toEqual([
      { op: 'equal', text: 'a' },
      { op: 'del', text: 'b' },
      { op: 'equal', text: 'c' },
    ]);
  });

  it('行の変更は del → add の順で表す（同一 hunk 内は削除が先）', () => {
    expect(diffLines('a\n価格は文中に記載\nc', 'a\n| プラン | 価格 |\nc')).toEqual([
      { op: 'equal', text: 'a' },
      { op: 'del', text: '価格は文中に記載' },
      { op: 'add', text: '| プラン | 価格 |' },
      { op: 'equal', text: 'c' },
    ]);
  });

  it('空 → テキストは全行 add / テキスト → 空は全行 del', () => {
    expect(diffLines('', 'x\ny')).toEqual([
      { op: 'add', text: 'x' },
      { op: 'add', text: 'y' },
    ]);
    expect(diffLines('x\ny', '')).toEqual([
      { op: 'del', text: 'x' },
      { op: 'del', text: 'y' },
    ]);
    expect(diffLines('', '')).toEqual([]);
  });

  it('重複行があっても LCS で最小の差分に揃う', () => {
    // 共通部分（a, b）を保ち、追加された b と c だけを差分にする
    expect(diffLines('a\nb', 'a\nb\na\nb')).toEqual([
      { op: 'equal', text: 'a' },
      { op: 'equal', text: 'b' },
      { op: 'add', text: 'a' },
      { op: 'add', text: 'b' },
    ]);
  });
});
