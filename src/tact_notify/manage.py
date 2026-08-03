"""課題マネジメント: digestチャンネルのテキストコマンドで daily の非表示を管理。

常駐サーバーが無い(GitHub Actionsのみ)のでボタンは使えない。代わりに
10分毎の check 実行時に conversations.history でチャンネルの新着メッセージを
読み、コマンドを適用して chat.postMessage で返信する(反映まで最大10分)。

番号は「最後に投稿した番号付きリスト」に対して解決する。リスト投稿時刻
(posted_at_epoch)より古い ts のコマンドは適用せず再投稿を促す(誤爆防止)。
非表示リストは state/seen.json に保存(キャッシュ消失時は再表示されるだけ)。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import config, slack_api, state
from .notify import days_left_label, fmt_dt
from .notion_sync import pending_of

MAX_COMMANDS_PER_RUN = 20  # spam guard, like check.MAX_ITEMS_PER_RUN

_VERBS = {
    "hide": "hide", "非表示": "hide",
    "show": "show", "表示": "show",
    "list": "list", "一覧": "list", "リスト": "list",
    "hidden": "hidden", "非表示一覧": "hidden",
    "help": "help", "ヘルプ": "help", "つかいかた": "help",
}

_USAGE = (
    "使い方: `hide 番号...`(非表示) / `show 番号...`(再表示) / "
    "`list`(未提出一覧) / `hidden`(非表示一覧)"
)


@dataclass
class Cmd:
    ts: str
    verb: str  # "hide" | "show" | "list" | "hidden" | "help" | "usage"
    nums: list[int] = field(default_factory=list)


def enabled() -> bool:
    return slack_api.enabled()


# keyword with digits glued on ("hide2", "非表示2") — longest keywords first so
# 非表示一覧 is not consumed by 非表示
_GLUED = re.compile(
    r"^(" + "|".join(sorted(_VERBS, key=len, reverse=True)) + r")(\d+)$",
    re.IGNORECASE,
)


def _parse(text: str, ts: str) -> Cmd | None:
    # commas (half/full width) count as separators; int() accepts full-width digits
    tokens = [t for t in re.split(r"[\s,、]+", text.strip()) if t]
    if not tokens:
        return None
    if m := _GLUED.match(tokens[0]):
        tokens[0:1] = [m.group(1), m.group(2)]
    verb = _VERBS.get(tokens[0].lower())
    if verb is None:
        return None  # not a command — stay silent (the channel also gets digests)
    if verb in ("hide", "show"):
        try:
            nums = [int(t) for t in tokens[1:]]
        except ValueError:
            return Cmd(ts, "usage")
        return Cmd(ts, verb, nums) if nums else Cmd(ts, "usage")
    return Cmd(ts, verb)


def read_commands(st: dict) -> tuple[list[Cmd], str | None]:
    """New commands in the digest channel since last run, plus the newest
    message ts seen. last_ts is advanced by apply_commands, not here — a crash
    in between just reprocesses (hide/show are idempotent)."""
    now_epoch = time.time()
    last_ts = st["manage"].get("last_ts")
    oldest = float(last_ts) if last_ts else now_epoch - 3600
    oldest = max(oldest, now_epoch - 86400)  # cache loss must not replay old commands
    messages = slack_api.fetch_messages(oldest=oldest)
    if not messages:
        return [], None
    cmds = [c for m in messages if (c := _parse(m.get("text", ""), m["ts"]))]
    return cmds, messages[-1]["ts"]


def purge_hidden(st: dict, all_items: list, now: datetime) -> None:
    """Drop hidden entries whose deadline passed (they no longer appear in the
    digest anyway) or whose item vanished from TACT."""
    current_ids = {a.id for a in all_items}
    for tid in list(st["hidden"]):
        due = st["hidden"][tid].get("due")
        expired = due is not None and datetime.fromisoformat(due) <= now
        if expired or tid not in current_ids:
            del st["hidden"][tid]


def format_pending_list(visible: list, now: datetime, hidden_count: int = 0) -> tuple[str, dict[str, str]]:
    """未提出・締切前の番号付き一覧(締切が早い順で渡すこと)と 番号->id 対応。"""
    footer = f"\n(非表示 {hidden_count}件 — `hidden` で確認)" if hidden_count else ""
    if not visible:
        return "未提出の課題はありません 🎉" + footer, {}
    lines = []
    num_map: dict[str, str] = {}
    for i, a in enumerate(visible, 1):
        left = days_left_label(a.due_time, now)
        prefix = "⚠️ " if left == "今日締切" else ""
        kind = "📝" if a.kind == "quiz" else ""
        lines.append(f"{i}. {prefix}{fmt_dt(a.due_time)}({left}) — {kind}[{a.site_title}] {a.title}")
        num_map[str(i)] = a.id
    header = f"📅 未提出の課題({now.month}/{now.day}時点・{len(visible)}件)"
    return header + "\n" + "\n".join(lines) + footer, num_map


def format_hidden_list(st: dict) -> tuple[str, dict[str, str]]:
    entries = sorted(st["hidden"].items(), key=lambda kv: kv[1].get("due") or "9999")
    if not entries:
        return "非表示中の課題はありません", {}
    lines = []
    num_map: dict[str, str] = {}
    for i, (tid, e) in enumerate(entries, 1):
        due = datetime.fromisoformat(e["due"]) if e.get("due") else None
        lines.append(f"{i}. [{e['site_title']}] {e['title']}(締切 {fmt_dt(due)})")
        num_map[str(i)] = tid
    header = f"🙈 非表示中の課題({len(entries)}件)— `show 番号` で再表示"
    return header + "\n" + "\n".join(lines), num_map


def record_map(st: dict, key: str, items: dict[str, str]) -> None:
    st["manage"]["list_gen"] += 1
    st["manage"][key] = {
        "gen": st["manage"]["list_gen"],
        "posted_at_epoch": time.time(),
        "items": items,
    }


def apply_commands(
    cmds: list[Cmd],
    newest_ts: str | None,
    st: dict,
    assignments: list,
    quizzes: list,
    now: datetime,
    dry_run: bool = False,
) -> None:
    if newest_ts is not None:
        st["manage"]["last_ts"] = newest_ts
    if not cmds:
        return
    purge_hidden(st, assignments + quizzes, now)
    by_id = {a.id: a for a in assignments + quizzes}
    notes: list[str] = []
    want_pending_list = False
    want_hidden_list = False
    changed = False

    for cmd in cmds[:MAX_COMMANDS_PER_RUN]:
        if cmd.verb in ("usage", "help"):
            notes.append(_USAGE)
            continue
        if cmd.verb == "list":
            want_pending_list = True
            continue
        if cmd.verb == "hidden":
            want_hidden_list = True
            continue
        # hide / show: resolve numbers against the matching list snapshot
        map_key = "pending_map" if cmd.verb == "hide" else "hidden_map"
        m = st["manage"].get(map_key)
        if m is None:
            notes.append("番号の記録がありません。最新の一覧の番号で送ってください。")
            want_pending_list |= cmd.verb == "hide"
            want_hidden_list |= cmd.verb == "show"
            continue
        if float(cmd.ts) < m["posted_at_epoch"]:
            notes.append("⚠️ 一覧が更新されているため適用しませんでした。最新の番号で送り直してください。")
            want_pending_list |= cmd.verb == "hide"
            want_hidden_list |= cmd.verb == "show"
            continue
        for n in cmd.nums:
            tid = m["items"].get(str(n))
            if tid is None:
                notes.append(f"番号 {n} は一覧にありません。")
            elif cmd.verb == "hide":
                a = by_id.get(tid)
                if a is None:
                    notes.append(f"番号 {n} の課題はTACT上に見つかりませんでした。")
                    continue
                st["hidden"][tid] = {
                    "title": a.title,
                    "site_title": a.site_title,
                    "due": a.due_time.isoformat() if a.due_time else None,
                    "hidden_at": state.now_iso(),
                }
                notes.append(f"✅ 非表示にしました: [{a.site_title}] {a.title}")
                changed = True
            else:  # show
                entry = st["hidden"].pop(tid, None)
                if entry is None:
                    notes.append(f"番号 {n} はすでに再表示されています。")
                else:
                    notes.append(f"👁️ 再表示しました: [{entry['site_title']}] {entry['title']}")
                    changed = True

    if len(cmds) > MAX_COMMANDS_PER_RUN:
        notes.append(f"(コマンドが多いため {len(cmds) - MAX_COMMANDS_PER_RUN} 件は無視しました)")

    # a posted list and its recorded map must always match what the user sees
    pending = pending_of(assignments, quizzes, now)
    visible = [a for a in pending if a.id not in st["hidden"]]
    if changed or want_pending_list:
        text, num_map = format_pending_list(visible, now, hidden_count=len(pending) - len(visible))
        record_map(st, "pending_map", num_map)
        if notes:
            notes.append("")
        notes.append(text)
    if want_hidden_list:
        text, num_map = format_hidden_list(st)
        record_map(st, "hidden_map", num_map)
        if notes and notes[-1] != "":
            notes.append("")
        notes.append(text)

    if notes:
        slack_api.post_message("\n".join(notes), dry_run=dry_run)


def run(dry_run: bool = False) -> None:
    """`manage` subcommand: process pending channel commands once (local testing;
    in CI the same logic runs inside every `check`)."""
    if not enabled():
        raise SystemExit("SLACK_BOT_TOKEN / SLACK_CHANNEL_DIGEST が未設定です(.env を確認)")
    st = state.load()
    cmds, newest_ts = read_commands(st)
    print(f"commands: {[(c.verb, c.nums) for c in cmds] or 'none'}")

    if cmds:
        from .sakai import fetch_assignments, fetch_quizzes, fetch_site_titles, mark_submitted_quizzes
        from .session import open_session

        client = open_session(config.SLACK_WEBHOOK_NOTIFY(), dry_run)
        try:
            titles = fetch_site_titles(client, course_only=False)
            assignments = fetch_assignments(client, titles)
            quizzes = fetch_quizzes(client, titles)
            now = datetime.now(config.JST)
            mark_submitted_quizzes(client, quizzes, now)
        finally:
            client.close()
        apply_commands(cmds, newest_ts, st, assignments, quizzes, now, dry_run)
    else:
        apply_commands([], newest_ts, st, [], [], datetime.now(config.JST), dry_run)

    if not dry_run:
        state.save(st)
