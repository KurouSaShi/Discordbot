import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# Flask app for health check
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))

# ======================
# 環境変数
# ======================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SHEET_API = os.getenv("SHEET_API_URL")
GUILD_IDS = [int(g) for g in os.getenv("GUILD_IDS", "").split(",") if g]

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
    """JSONファイルを安全に読み込む"""
    if not os.path.exists(path):
        print(f"File not found: {path}, creating with default value")
        save_json(path, default)
        return default
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                print(f"Empty file: {path}, using default value")
                save_json(path, default)
                return default
            return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in {path}: {e}, using default value")
        save_json(path, default)
        return default
    except Exception as e:
        print(f"Error loading {path}: {e}, using default value")
        return default

def save_json(path, data):
    """JSONファイルを安全に保存"""
    try:
        # 一時ファイルに書き込んでから置き換え(atomic write)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 正常に書き込めたら元のファイルを置き換え
        os.replace(temp_path, path)
    except Exception as e:
        print(f"Error saving {path}: {e}")
        # 一時ファイルが残っていたら削除
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

def load_charters():
    return load_json(DATA_FILE, {})

def save_charters(data):
    save_json(DATA_FILE, data)

def load_notified():
    return load_json(NOTIFY_FILE, {})

def save_notified(data):
    save_json(NOTIFY_FILE, data)

# 非同期API呼び出し
async def fetch_sheet_data():
    """非同期でスプレッドシートデータを取得"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SHEET_API, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"API Error: {response.status}")
                    return []
    except Exception as e:
        print(f"Failed to fetch sheet data: {e}")
        return []

# ======================
# Bot
# ======================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    
    # 初回起動時にファイルを初期化
    load_charters()
    load_notified()
    print("JSON files initialized")

    # コマンド同期
    for guild_id in GUILD_IDS:
        guild_obj = discord.Object(id=guild_id)
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} commands for guild {guild_id}")
        except Exception as e:
            print(f"Failed to sync guild {guild_id}: {e}")

    # 期限チェックタスクを開始
    if not deadline_check.is_running():
        deadline_check.start()

    print("Bot ready & all commands synced")


# ======================
# /get
# ======================
@bot.tree.command(name="get", guilds=[discord.Object(id=g) for g in GUILD_IDS])
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
    # すぐに応答を返す（3秒制限対策）
    await interaction.response.defer()

    selected_status = status.value if status else DEFAULT_STATUS
    
    # 非同期でデータ取得
    rows = await fetch_sheet_data()

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

    if not rows:
        await interaction.followup.send("🔍 該当する曲はありません")
        return

    embed = discord.Embed(title="🎵 曲一覧", color=0x5865F2)

    for r in rows:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r['ステータス'],'❓')} {r['曲名']} / {r['作曲者']}",
            value=(
                f"**Sp**:{r.get('Sp','-')}\n"
                f"**Sm**:{r.get('Sm','-')}\n"
                f"**Am**:{r.get('Am','-')}\n"
                f"**Wt**:{r.get('Wt','-')}"
            ),
            inline=False
        )

    embed.set_footer(text=f"凡例:{STATUS_LEGEND}")
    await interaction.followup.send(embed=embed)


# ======================
# /search
# ======================
@bot.tree.command(name="search", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def search(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()

    rows = await fetch_sheet_data()

    rows = [
        r for r in rows
        if isinstance(r, dict)
        and r.get("曲名")
        and r.get("作曲者")
    ]

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

    embed = discord.Embed(title="🎵 曲一覧", color=0x5865F2)

    for r in rows[:10]:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r.get('ステータス'),'❓')} "
                 f"{r['曲名']} / {r['作曲者']}",
            value=(
                f"**Sp**:{r.get('Sp','-')}\n"
                f"**Sm**:{r.get('Sm','-')}\n"
                f"**Am**:{r.get('Am','-')}\n"
                f"**Wt**:{r.get('Wt','-')}"
            ),
            inline=False
        )

    embed.set_footer(text=f"凡例:{STATUS_LEGEND}")
    await interaction.followup.send(embed=embed)


# ======================
# /listadd
# ======================
@bot.tree.command(name="listadd", guilds=[discord.Object(id=g) for g in GUILD_IDS])
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
@bot.tree.command(name="list", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def list_cmd(interaction: discord.Interaction):
    data = load_charters()
    user_map = {}

    for name, users in data.items():
        for uid in users:
            user_map.setdefault(uid, []).append(name)

    if not user_map:
        await interaction.response.send_message("📭 登録なし")
        return

    embed = discord.Embed(title="📋 Charter一覧", color=0x57F287)

    for uid, names in user_map.items():
        member = interaction.guild.get_member(uid)
        mention = member.mention if member else f"<@{uid}>"
        embed.add_field(
            name="",
            value=f"{mention}\n" + " / ".join(sorted(names)),
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# ======================
# /listopt
# ======================
@bot.tree.command(name="listopt", guilds=[discord.Object(id=g) for g in GUILD_IDS])
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
@bot.tree.command(name="deadline", description="自分の作業中・優先作業タスクをDMで確認", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def deadline(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rows = await fetch_sheet_data()
    charter_map = load_charters()

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

        if r.get("ステータス") not in valid_status:
            continue

        date_str = r.get("本収録日")
        if not date_str:
            continue

        try:
            target = datetime.strptime(date_str, "%Y/%m/%d")
        except ValueError:
            continue

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
                f"**担当難易度**:{' / '.join(matched_diffs)}\n"
                f"**納期**:<t:{timestamp}:R>"
            ),
            inline=False
        )

    if not found:
        await interaction.followup.send(
            "📭 現在、担当中のタスクはありません",
            ephemeral=True
        )
        return

    try:
        await interaction.user.send(embed=embed)
        await interaction.followup.send(
            "📬 DMに担当中タスクを送信しました",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ DMを送信できませんでした。DMを受け取れる設定にしてください",
            ephemeral=True
        )


# ======================
# 納期自動DM
# ======================
@tasks.loop(hours=24)
async def deadline_check():
    try:
        rows = await fetch_sheet_data()
        
        if not rows:
            print("No data fetched for deadline check")
            return

        today = datetime.now(timezone.utc).date()
        charters = load_charters()
        notified = load_notified()

        for r in rows:
            if not isinstance(r, dict):
                continue

            status = str(r.get("ステータス","")).strip()
            if not any(s in status for s in ("作業中","優先作業")):
                continue

            date_str = str(r.get("本収録日","")).strip()
            title = r.get("曲名","不明")

            try:
                target = datetime.strptime(date_str, "%Y/%m/%d").date()
                if target.year < 1971:
                    continue
            except Exception:
                continue

            diff_map = {}
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

            for days, tag in ((21,"week3"), (14,"week2")):
                key = f"{title}_{date_str}_{tag}"
                if today != target - timedelta(days=days):
                    continue
                if key in notified:
                    continue

                for uid, diffs in diff_map.items():
                    try:
                        user = bot.get_user(uid) or await bot.fetch_user(uid)

                        if not any(bot.get_guild(gid) and bot.get_guild(gid).get_member(uid) for gid in GUILD_IDS):
                            continue

                        await user.send(
                            f"⏰ 納期通知 ({days}日前)\n"
                            f"{title}\n"
                            f"担当:{' / '.join(diffs)}\n"
                            f"納期:{date_str}"
                        )
                        print(f"DM sent to {user} ({uid})")
                    except Exception as e:
                        print(f"Failed to send DM to {uid}: {e}")

                notified[key] = today.isoformat()

        save_notified(notified)
    except Exception as e:
        print(f"Error in deadline_check task: {e}")


# ======================
# 起動
# ======================
if __name__ == "__main__":
    # Start Flask in a separate thread
    Thread(target=run_flask, daemon=True).start()
    # Start Discord bot
    bot.run(TOKEN)
