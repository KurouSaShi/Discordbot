import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

GUILD_IDS = [int(g) for g in os.getenv("GUILD_IDS", "").split(",") if g]


# ======================
# 環境変数
# ======================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SHEET_API = os.getenv("SHEET_API_URL")

if not TOKEN or not SHEET_API:
    raise RuntimeError("環境変数が不足しています")

DATA_FILE = "charter_users.json"
NOTIFY_FILE = "sent_notifications.json"

# ======================
# 定数
# ======================
DEFAULT_STATUS = "作業中"
PER_PAGE = 10

STATUS_LIST = [
    "未割当", "作業中", "優先作業", "準作業",
    "調整中", "配信待ち", "完了", "期間限定"
]

STATUS_EMOJI = {
    "未割当": "⬜",
    "作業中": "🟨",
    "優先作業": "🔴",
    "準作業": "🟦",
    "調整中": "🟪",
    "配信待ち": "🟩",
    "完了": "✅",
    "期間限定": "⏳"
}

STATUS_LEGEND = " ".join(f"{v} {k}" for k, v in STATUS_EMOJI.items())

# ======================
# ユーティリティ
# ======================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_charters():
    return load_json(DATA_FILE, {})

def save_charters(data):
    save_json(DATA_FILE, data)

def load_notified():
    return load_json(NOTIFY_FILE, {})

def save_notified(data):
    save_json(NOTIFY_FILE, data)

def user_aliases(user_id: int, charter_map: dict) -> list[str]:
    return [name for name, users in charter_map.items() if user_id in users]

# ======================
# Bot
# ======================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    for guild_id in GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if guild:
            await bot.tree.sync(guild=guild)
    if not deadline_check.is_running():
        deadline_check.start()
    print("Bot ready & synced for specified guilds")


# ======================
# /get
# ======================
@bot.tree.command(name="get",guild_ids=GUILD_IDS)
@app_commands.describe(
    status="ステータス",
    count="件数",
    include_unassigned="未割当を含める",
    charter="難易度に含まれる文字列"
)
@app_commands.choices(
    status=[app_commands.Choice(name=s, value=s) for s in STATUS_LIST]
)
async def get(
    interaction: discord.Interaction,
    status: app_commands.Choice[str] | None = None,
    count: int = 10,
    include_unassigned: bool = False,
    charter: str | None = None
):
    await interaction.response.defer()

    selected_status = status.value if status else DEFAULT_STATUS
    rows = requests.get(SHEET_API).json()

    rows = [
        r for r in rows
        if isinstance(r, dict)
        and r.get("曲名")
        and r.get("作曲者")
        and r.get("ステータス") == selected_status
    ]

    if not include_unassigned:
        rows = [r for r in rows if r["ステータス"] != "未割当"]

    if charter:
        rows = [
            r for r in rows
            if any(charter in str(r.get(c, "")) for c in ("Sp", "Sm", "Am", "Wt"))
        ]

    rows = rows[-count:]

    embed = discord.Embed(title="🎵 曲一覧", color=0x5865F2)

    for r in rows:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r['ステータス'],'❓')} {r['曲名']} / {r['作曲者']}",
            value=(
                f"**Sp**：{r.get('Sp','-')}\n"
                f"**Sm**：{r.get('Sm','-')}\n"
                f"**Am**：{r.get('Am','-')}\n"
                f"**Wt**：{r.get('Wt','-')}"
            ),
            inline=False
        )

    embed.set_footer(text=f"凡例：{STATUS_LEGEND}")
    await interaction.followup.send(embed=embed)

# ======================
# /search
# ======================
@bot.tree.command(name="search",guild_ids=GUILD_IDS)
async def search(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()

    rows = requests.get(SHEET_API).json()

    # 空行・型不正を除外
    rows = [
        r for r in rows
        if isinstance(r, dict)
        and r.get("曲名")
        and r.get("作曲者")
    ]

    # keyword 検索（曲名も含む）
    rows = [
        r for r in rows
        if (
            keyword in str(r.get("曲名",""))
            or keyword in str(r.get("作曲者",""))
            or any(keyword in str(r.get(c,"")) for c in ("Sp","Sm","Am","Wt"))
        )
    ]

    if not rows:
        await interaction.followup.send("🔍 該当する曲はありません")
        return

    embed = discord.Embed(
        title="🎵 曲一覧",
        color=0x5865F2
    )

    for r in rows[:10]:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r.get('ステータス'),'❓')} "
                 f"{r['曲名']} / {r['作曲者']}",
            value=(
                f"**Sp**：{r.get('Sp','-')}\n"
                f"**Sm**：{r.get('Sm','-')}\n"
                f"**Am**：{r.get('Am','-')}\n"
                f"**Wt**：{r.get('Wt','-')}"
            ),
            inline=False
        )

    embed.set_footer(text=f"凡例：{STATUS_LEGEND}")
    await interaction.followup.send(embed=embed)



# ======================
# /listadd
# ======================
@bot.tree.command(name="listadd",guild_ids=GUILD_IDS)
async def listadd(interaction: discord.Interaction, name: str, user: discord.User):
    data = load_charters()
    data.setdefault(name, [])
    if user.id not in data[name]:
        data[name].append(user.id)
        save_charters(data)
    await interaction.response.send_message("✅ 追加しました")

# ======================
# /list
# ======================
@bot.tree.command(name="list",guild_ids=GUILD_IDS)
async def list_cmd(interaction: discord.Interaction):
    data = load_charters()
    user_map = {}

    for name, users in data.items():
        for uid in users:
            user_map.setdefault(uid, []).append(name)

    embed = discord.Embed(title="📋 Charter一覧", color=0x57F287)

    for uid, names in user_map.items():
        member = interaction.guild.get_member(uid)
        mention = member.mention if member else f"<@{uid}>"
        embed.add_field(
            name="",
            value=f"{mention}\n" + " / ".join(sorted(names)),
            inline=False
        )

    await interaction.response.send_message(embed=embed if user_map else "登録なし")

# ======================
# /listopt
# ======================
@bot.tree.command(name="listopt",guild_ids=GUILD_IDS)
@app_commands.choices(
    action=[
        app_commands.Choice(name="追加", value="add"),
        app_commands.Choice(name="削除", value="remove")
    ]
)
async def listopt(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    user: discord.User,
    new_name: str
):
    data = load_charters()
    uid = user.id

    if action.value == "add":
        data.setdefault(new_name, [])
        if uid not in data[new_name]:
            data[new_name].append(uid)
            save_charters(data)
        await interaction.response.send_message("✅ 名義を追加しました")
    else:
        if new_name in data and uid in data[new_name]:
            data[new_name].remove(uid)
            if not data[new_name]:
                del data[new_name]
            save_charters(data)
            await interaction.response.send_message("🗑️ 削除しました")
        else:
            await interaction.response.send_message("❌ 紐づいていません")

# ======================
# /deadline
# ======================
@bot.tree.command(name="deadline", description="自分の作業中・優先作業タスクをDMで確認",guild_ids=GUILD_IDS)
async def deadline(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rows = requests.get(SHEET_API).json()
    charter_map = load_charters()

    # 自分の全名義（例: ["黒兎氏", "veal"]）
    my_aliases = [
        name for name, users in charter_map.items()
        if interaction.user.id in users
    ]

    if not my_aliases:
        await interaction.followup.send(
            "❌ あなたの名義が /list に登録されていません",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="⏰ 担当中のタスク",
        color=0xFEE75C
    )

    found = False
    valid_status = {"作業中", "優先作業"}

    for r in rows:
        if not isinstance(r, dict):
            continue

        # ステータス条件
        if r.get("ステータス") not in valid_status:
            continue

        date_str = r.get("本収録日")
        if not date_str:
            continue

        try:
            target = datetime.strptime(date_str, "%Y/%m/%d")
        except ValueError:
            continue

        # 難易度チェック（U〜X列）
        matched_diffs = []
        for diff in ("Sp", "Sm", "Am", "Wt"):
            cell = str(r.get(diff, ""))
            if any(alias in cell for alias in my_aliases):
                matched_diffs.append(diff)

        if not matched_diffs:
            continue

        found = True
        timestamp = int(target.timestamp())

        embed.add_field(
            name=r.get("曲名", "不明"),
            value=(
                f"**担当難易度**：{' / '.join(matched_diffs)}\n"
                f"**納期**：<t:{timestamp}:R>"
            ),
            inline=False
        )

    if not found:
        await interaction.followup.send(
            "📭 現在、担当中のタスクはありません",
            ephemeral=True
        )
        return

    # DM送信
    await interaction.user.send(embed=embed)
    await interaction.followup.send(
        "📬 DMに担当中タスクを送信しました",
        ephemeral=True
    )



# ======================
# 納期自動DM
# ======================
@tasks.loop(hours=24)
async def deadline_check():
    try:
        rows = requests.get(SHEET_API).json()
    except Exception as e:
        print("Failed to fetch Sheet:", e)
        return

    today = datetime.now(timezone.utc).date()
    charters = load_charters()   # {名義: [UID,...]}
    notified = load_notified()   # {キー: 日付}

    for r in rows:
        if not isinstance(r, dict):
            continue

        # ステータス判定
        status = str(r.get("ステータス","")).strip()
        if not any(s in status for s in ("作業中","優先作業")):
            continue

        date_str = str(r.get("本収録日","")).strip()
        title = r.get("曲名","不明")

        # 日付変換
        try:
            target = datetime.strptime(date_str, "%Y/%m/%d").date()
            if target.year < 1971:
                continue
        except Exception:
            continue

        # 難易度列 U~X と名義マッチ
        diff_map = {}  # uid(int) -> set of diff
        for diff in ("Sp","Sm","Am","Wt"):
            cell = str(r.get(diff,"")).strip()
            for name, uid_list in charters.items():
                if name in cell:
                    for uid in uid_list:
                        try:
                            uid_int = int(uid)
                            diff_map.setdefault(uid_int, set()).add(diff)
                        except Exception as e:
                            print(f"Invalid UID {uid} for name {name}: {e}")

        if not diff_map:
            continue

        # 通知判定
        for days, tag in ((21,"week3"), (14,"week2")):
            key = f"{title}_{date_str}_{tag}"
            if today != target - timedelta(days=days):
                continue
            if key in notified:
                continue

            # DM送信（対象サーバー所属ユーザーのみ）
            for uid, diffs in diff_map.items():
                try:
                    # ユーザー取得
                    user = bot.get_user(uid) or await bot.fetch_user(uid)

                    # 所属サーバーチェック
                    if not any(bot.get_guild(gid) and bot.get_guild(gid).get_member(uid) for gid in GUILD_IDS):
                        continue  # 指定サーバーにいなければスキップ

                    await user.send(
                        f"⏰ 納期通知 ({days}日前)\n"
                        f"{title}\n"
                        f"担当：{' / '.join(diffs)}\n"
                        f"納期：{date_str}"
                    )
                    print(f"DM sent to {user} ({uid})")
                except Exception as e:
                    print(f"Failed to send DM to {uid}: {e}")

            # 送信済み登録
            notified[key] = today.isoformat()

    save_notified(notified)

# ======================
# 起動
# ======================
bot.run(TOKEN)
