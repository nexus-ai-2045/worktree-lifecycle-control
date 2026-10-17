# 0004. 到達不能でも内容が取り込み済みなら blocker から降格する

- 状態: 採択
- 日付: 2026-09-27
- 影響: blocker `head_becomes_unreachable` の条件を狭める。signal `head_unreachable_content_integrated` と observation `unreachable_content_proof` を追加する。schema 版は変えない (どちらも既存の自由形式の欄に載る)

## 背景

[ADR 0002](0002-protect-what-git-does-not.md) は「worktree を消すと HEAD が到達不能になる」を唯一の git 由来 blocker にした。

2026-09-25 の実測で、この blocker に当たった 2 件はどちらも実損ゼロの誤検知だった。

| repo | worktree の HEAD | 実際の形 |
| --- | --- | --- |
| Projects | `0bc1ab13` | 作業 branch に main を merge → PR #721 を squash merge (`1c1e0ae2e`) → branch 削除 |
| nexus_ai | `6f10d39e` | 作業 branch に main を merge → 各 commit が patch 同一で main に載る → branch 削除 |

squash / rebase merge すると、元の commit は main の履歴に入らない。branch を消した時点で、worktree に残った HEAD は必ず到達不能になる。到達可能性だけで判定する限り、PR を squash で取り込むたびに誤検知が 1 件増える。危険表示が誤検知で埋まると、本物の 1 件が見落とされる。

## 脅威モデル

| 項目 | 内容 |
| --- | --- |
| 誰から | 悪意ではなく運用事故。squash merge 後に branch だけ消され worktree が残る。人や agent が危険表示を見慣れて読み飛ばす |
| 何を | worktree の HEAD にしか無い内容 (ファイルの中身)。削除と `git gc` の後に取り戻せなくなるもの |
| どうなると困る | 誤って降格すると、唯一の写しが入った worktree が `cleanup_candidate` に並び、人が消して内容を失う。降格しないままだと、誤検知の山に本物が埋もれる |
| 守らないもの | commit メッセージ・途中の状態・author 情報 (squash merge 自体が捨てるもので、PR に残る)。base 側の履歴が書き換えられる可能性 (base は remote-tracking ref を信用する)。証明に上限を超える計算が要る巨大な branch (証明せず保護する) |

## 決定

到達不能になる commit 集合 (`git rev-list <head> --not --branches --tags --remotes`) について、次の 2 つが両方とも成り立つと証明できた時だけ、`head_becomes_unreachable` を blocker から外し、`head_unreachable_content_integrated` を signal に出す。

1. 集合内の merge commit は、すべて親が 2 つで、`git merge-tree --write-tree <親1> <親2>` の tree と一致する (衝突解消や手で足した内容を持たない)。
2. 集合内の非 merge commit について、次のどちらかが成り立つ。
   - `patch_equivalent`: 空白を剥がさない patch-id (`git patch-id --verbatim`、差分は `--binary`) が等しい commit が、base にあって head に無い commit の中にある。rebase merge / cherry-pick の形。
   - `tree_match`: それらの commit が触ったパスについて、head の内容と完全に一致する commit が base の履歴上に 1 つある (`git diff --quiet <その commit> <head> -- <パス>`)。複数 commit を 1 つに潰した squash merge の形。

どれかを測れない・満たさない時は証明なし (`None`) とし、従来どおり保護する。根拠 (方法・base ref・一致した commit・比較したパス・自動 merge と一致した merge commit) は `observations.unreachable_content_proof` にそのまま出す。

### 1 が要る理由

`git cherry` は merge commit を比較しない。1 を省くと、merge commit にだけ入った内容 (evil merge) は非 merge commit が全部取り込み済みでも見逃され、黙って消える。`tests/test_reachability.py::test_evil_merge_content_blocks_the_proof` がこの穴を固定している。

### `git cherry` を証明に使わない理由

`git cherry` の既定の patch-id は空白を無視する。インデントだけ違う版が base にあっても `-` と出るので、YAML のようにインデントが意味を持つ内容では「同じ内容」を意味しない。統合状態の signal (`branch_integration`) には今までどおり使うが、唯一の写しを外す証明には verbatim patch-id を使う。PR #21 のレビューで指摘され、`test_whitespace_only_difference_is_not_proof_of_integration` が固定している。

`--binary` を付けないと、別々のバイナリ変更が同じ "Binary files differ" に潰れて一致する。patch が空になる変更 (mode だけの変更など) は patch-id が出ないので、証明なしに倒れる。

### パス名は NUL だけで区切る

`-z` の出力から前後の改行を剥がすと、改行で始まり改行で終わるパス (`"\nsecret\n"`) が別のパス `secret` に化け、無関係なパスを比べて一致と誤判定する。PR #21 のレビューで指摘され、`test_newline_in_path_is_not_normalized_away` が固定している。

### 2 の比較対象から merge 経由のパスを外す理由

`git log -m` で merge の差分まで含めると、main から merge で入ってきたファイルも比較対象になる。main がその後そのファイルを変えていると、squash commit とも一致しなくなる。実測で `0bc1ab13` がこの形だった (merge 経由の 2 ファイルで不一致、枝で書いた 4 ファイルは squash commit `1c1e0ae2e` と一致)。1 で merge が独自の内容を持たないと確かめてあるので、外しても失われる内容は無い。

## 却下した代替案

- **行単位で「head にしか無い行が無い」を見る** — 削除や並べ替えを表せず、証明にならない。
- **パスごとに別々の base commit との一致を許す** — 内容は保たれるが、「どの 1 commit と同じか」を根拠として示せなくなる。人が確かめる時の手間が増えるので、1 commit との一致に限る。
- **ADR 0002 の「squash 検出に追加ヒューリスティクスを入れない」との関係** — あちらは signal (表示) の精度の話で、取りこぼしても blocker は動かなかった。今回は blocker を外す判定なので、推測ではなく git の事実で証明できる 2 方法に限った。

## Invariant

- 証明できない時は必ず保護する。`None` を「取り込み済み」と読まない。
- 降格しても signal は必ず出す。消してよいかの最終判断は人がする。
- 探索は上限付き (`TREE_MATCH_CANDIDATE_LIMIT`、git 1 回あたり 60 秒)。上限を超えたら証明なしとする。

## Evidence

- 2026-09-27 本実装を実 repo に当てた結果: `0bc1ab13` は `tree_match` (一致 commit `1c1e0ae2e`、比較 4 パス、clean merge `823c33666`)、`6f10d39e` は `patch_equivalent` (到達不能 14 commit、clean merge `59a2e3305`)。
- 再現テスト: `tests/test_reachability.py` の `test_rebase_merged_branch_with_clean_merge_is_proven_integrated` / `test_squash_merged_branch_with_clean_merge_is_proven_by_tree_match`。安全側は `test_unique_detached_commit_has_no_proof` / `test_partially_integrated_branch_has_no_proof` / `test_evil_merge_content_blocks_the_proof` / `test_whitespace_only_difference_is_not_proof_of_integration` / `test_newline_in_path_is_not_normalized_away` / `test_proof_is_none_when_it_cannot_be_measured`。
- verbatim patch-id に変えた後の所要時間 (実測): `6f10d39e` は 18 秒 (base 側の差分全体を読むため。`git cherry` では 0.8 秒)、`0bc1ab13` は 10 秒。対象は到達不能な worktree だけ。
