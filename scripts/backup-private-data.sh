#!/bin/bash
# 非公開データを Mac の外へ退避する。
#
#   scripts/backup-private-data.sh [出力先ディレクトリ] [--prune]
#
# 既定では古い世代を消さない。超過分を一覧表示するだけなので、消してよいと
# 判断したときに --prune を付けて実行する。
#
# GitHub にはソースしか入っていないため、カード正本・頻出度監査・回答履歴は
# Mac が壊れると戻せない。ここで守るのはその復元不能な部分である。
#
# 中身には合格道場から取得した過去問原文と provider 解説が含まれる。保存先は
# LAN 内の Windows 共有であり、暗号化はしない方針（2026-07-29 に利用者が判断）。
# ただし Web 公開領域や外部ストレージへは置かないこと。

set -euo pipefail

RUNTIME="$HOME/.local/share/yuki-services/gyousei-lab"
DEST=""
PRUNE=0
for arg in "$@"; do
    case "$arg" in
        --prune) PRUNE=1 ;;
        *) DEST="$arg" ;;
    esac
done
DEFAULT_DEST="$HOME/mnt/win-new-folder/gyousei-lab-backup"
# 保存先を明示しなかったとき（＝既定のWindows共有へ書くとき）だけ、
# 本当にマウントされているかを確かめる。
REQUIRE_MOUNT=0
if [ -z "$DEST" ]; then
    DEST="$DEFAULT_DEST"
    REQUIRE_MOUNT=1
fi
KEEP=7
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$1"; }
die() { printf 'backup failed: %s\n' "$1" >&2; exit 1; }

[ -d "$RUNTIME" ] || die "実行時データが見つからない: $RUNTIME"

# Windows が落ちていると共有が外れる。そのまま mkdir すると、同じパスの
# ローカルディレクトリへ書いてしまい「Mac外に控えがある」と誤認する。
# 親ディレクトリのデバイス番号が変わるかどうかでマウント境界を判定する。
if [ "$REQUIRE_MOUNT" -eq 1 ]; then
    MOUNT_ROOT="$(dirname "$DEST")"
    if [ ! -d "$MOUNT_ROOT" ] \
        || [ "$(stat -f %d "$MOUNT_ROOT")" = "$(stat -f %d "$MOUNT_ROOT/..")" ]; then
        die "$MOUNT_ROOT がマウントされていない。Windows側の共有を接続してから実行する。
（Mac内へ書いても Mac が壊れたときの備えにならないため、ここで止める）"
    fi
fi

mkdir -p "$DEST" || die "出力先を作れない: $DEST"
[ -w "$DEST" ] || die "出力先へ書き込めない: $DEST"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/gyousei-backup.XXXXXX")"
# 消すのは mktemp で自分が作った一時領域だけに限る。
# 出力先のバックアップ本体は、このスクリプトからは既定で消さない。
cleanup() { [ -n "${WORK:-}" ] && [ -d "$WORK" ] && /bin/rm -rf -- "$WORK"; }
trap cleanup EXIT

STAGE="$WORK/gyousei-lab-$STAMP"
mkdir -p "$STAGE"

# SQLite は稼働中なので単純な cp を使わない。backup API で整合したコピーを取る。
log "SQLite をバックアップAPIで取得"
/opt/homebrew/bin/python3.12 - "$RUNTIME/production.sqlite3" "$STAGE/production.sqlite3" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
src = sqlite3.connect("file:" + source + "?mode=ro", uri=True)
dst = sqlite3.connect(target)
src.backup(dst)
dst.close()
src.close()

# 検査は書き込み可で開く。read-only で開くと WAL を畳めず、-wal と -shm が
# アーカイブへ残ってしまう。復元時に「どれが正本か」を迷わせるので、
# ここで 1 ファイルへまとめきる。本番DBではなくコピーに対する操作である。
check = sqlite3.connect(target)
result = check.execute("PRAGMA quick_check").fetchone()[0]
rows = check.execute("SELECT COUNT(*) FROM card_attempts").fetchone()[0]
marks = check.execute("SELECT COUNT(*) FROM card_marks").fetchone()[0]
check.execute("PRAGMA wal_checkpoint(TRUNCATE)")
# server.py は起動時に自分で WAL へ戻すので、控え側は DELETE でよい。
check.execute("PRAGMA journal_mode = DELETE")
check.close()
if result != "ok":
    raise SystemExit(f"quick_check が ok ではない: {result}")
print(f"  quick_check=ok card_attempts={rows} card_marks={marks}")
PY

log "復元不能なデータを集める"
# canonical: カード正本と⑤の出典。手で書いたもので再生成できない。
# curation:  頻出度監査と類似候補の判断。人の判断が入っている。
# all_subjects: 合格道場からの取得結果。再取得は可能だが、提供が終われば戻せない。
# builds/ と backups/ は canonical と SQLite から作り直せるので入れない。
for path in \
    "authoring/canonical" \
    "authoring/curation" \
    "authoring/all_subjects" \
    "authoring/incoming" \
    "analytics"
do
    if [ -e "$RUNTIME/$path" ]; then
        mkdir -p "$STAGE/$(dirname "$path")"
        cp -R "$RUNTIME/$path" "$STAGE/$path"
    fi
done
for file in "gyousei-production.json" "all-subject-inventory.json" "weakness-latest.json"; do
    [ -f "$RUNTIME/$file" ] && cp "$RUNTIME/$file" "$STAGE/$file"
done

cat > "$STAGE/RESTORE.md" <<EOF
# 復元手順

作成日時: $STAMP
作成元: $RUNTIME

## 中身

合格道場から取得した過去問原文と provider 解説を含む。**Web公開領域へ置かない。**

## 展開

\`\`\`bash
shasum -a 256 -c <(printf '%s  %s\\n' "\$(cat gyousei-lab-$STAMP.tar.gz.sha256)" gyousei-lab-$STAMP.tar.gz)
tar xzf gyousei-lab-$STAMP.tar.gz
\`\`\`

## 戻し方

1. \`~/.local/share/yuki-services/gyousei-lab/\` を作る。
2. 展開した \`authoring/\`、\`analytics/\`、各 json、\`production.sqlite3\` を置く。
3. 権限を \`0600\` にする。
4. \`PRAGMA quick_check\` を実行する。
5. アプリのソースは GitHub の \`yamashita-yukihito/gyousei-lab\` から取る。
6. bundle は canonical から作り直せる。手順は \`docs/SESSION_HANDOFF_20260728.md\`。
EOF

log "tar でまとめる"
OUT="$DEST/gyousei-lab-$STAMP.tar.gz"
tar czf "$OUT" -C "$WORK" "gyousei-lab-$STAMP"
chmod 600 "$OUT" 2>/dev/null || true

# 復元前に壊れていないか確かめられるよう、ハッシュを別ファイルへ残す
shasum -a 256 "$OUT" | awk '{print $1}' > "$OUT.sha256"

# 書き込み先は共有越しなので、途中で切れていないかをここで確かめる。
log "検証: 書き出したtarを読めるか"
tar tzf "$OUT" > /dev/null || die "書き出したアーカイブを読めない"

EXPIRED="$(ls -1t "$DEST"/gyousei-lab-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)"
if [ -n "$EXPIRED" ]; then
    COUNT="$(printf '%s\n' "$EXPIRED" | wc -l | tr -d ' ')"
    if [ "$PRUNE" -eq 1 ]; then
        log "古い世代を削除（$KEEP 世代を残す）"
        printf '%s\n' "$EXPIRED" | while read -r old; do
            [ -n "$old" ] || continue
            log "  削除: $(basename "$old")"
            /bin/rm -f -- "$old" "$old.sha256"
        done
    else
        log "$KEEP 世代を超えた古いバックアップが $COUNT 件ある（消していない）"
        printf '%s\n' "$EXPIRED" | while read -r old; do
            [ -n "$old" ] || continue
            log "  超過: $(basename "$old")"
        done
        log "消してよければ --prune を付けて実行する"
    fi
fi

log "完了: $OUT （$(du -h "$OUT" | cut -f1)）"
