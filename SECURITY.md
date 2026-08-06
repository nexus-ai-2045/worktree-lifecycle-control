# セキュリティポリシー

## 報告

脆弱性の疑いは、確認前に公開 issue へ投稿しないでください。private 報告経路が使える場合はそちらを使ってください。使えない場合は repository owner へ非公開で連絡してください。

報告には、影響範囲、再現手順、想定される悪用方法を含めてください。secret、個人情報、第三者の非公開データは必要最小限にし、公開 issue / pull request に添付しないでください。

## 対象

最新の `main` をサポート対象とします。本ツールは read-only の棚卸しと cleanup readiness 判定までを扱い、削除や GitHub write は実行しません。scanner / gate の結果はヒューリスティックであり、専用 secret scanner や人間レビューを置き換えません。

## データ

scan report や review packet には path、HEAD SHA、owner/task 台帳メタデータが含まれる場合があります。credential 本体、private conversation、個人名を fixture や commit へ入れないでください。
