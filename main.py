import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from aiohttp import web, ClientSession, ClientTimeout
import asyncio

# ======================
# 環境変数読み込み
# ======================
load_dotenv()

GUILD_IDS = [int(g) for g in os.getenv("GUILD_IDS", "").split(",") if g]

TOKEN = os.getenv("DISCORD_TOKEN")
SHEET_API = os.getenv("SHEET_API_URL")
PORT = int(os.getenv("PORT", 8000,))

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
# JSONユーティリティ
# ======================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Warning: Failed to load {path}: {e}. Using default.")
        return default

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
# ヘルスチェックサーバー
# ======================
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Health check server running on port {PORT}")

# ======================
# Bot初期化
# ======================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ======================
# 非同期APIヘルパー
# ======================
async def fetch_sheet(session: ClientSession):
    try:
        async with session.get(SHEET_API, timeout=ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        print(f"API request failed: {e}")
        return None

# ======================
# Bot起動イベント
# ======================
@bot.event
async def on_ready():
    asyncio.create_task(start_web_server())
    
    synced_count = 0
    for guild_id in GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if guild:
            try:
                synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
                synced_count += 1
                print(f"Synced {len(synced)} commands to guild: {guild.name} ({guild_id})")
            except Exception as e:
                print(f"Failed to sync commands to guild {guild_id}: {e}")
    
    if not deadline_check.is_running():
        deadline_check.start()
    
    print(f"Bot ready! Logged in as {bot.user}")
    print(f"Successfully synced commands to {synced_count}/{len(GUILD_IDS)} guilds")

# ======================
# /ping
# ======================
@bot.tree.command(name="ping", description="Botの動作確認", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is working!")

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
    await interaction.response.defer()
    selected_status = status.value if status else DEFAULT_STATUS
    
    async with ClientSession() as session:
        rows = await fetch_sheet(session)
    
    if rows is None:
        await interaction.followup.send("❌ APIへのアクセスに失敗しました", ephemeral=True)
        return
    
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
        rows = [r for r in rows if any(charter in str(r.get(c, "")) for c in ("Sp","Sm","Am","Wt"))]

    rows = rows[-count:]
    
    if not rows:
        await interaction.followup.send("該当する曲が見つかりませんでした")
        return

    embed = discord.Embed(title="🎵 曲一覧", color=0x5865F2)
    for r in rows:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r['ステータス'],'❓')} {r['曲名']} / {r['作曲者']}",
            value=f"**Sp**:{r.get('Sp','-')}\n**Sm**:{r.get('Sm','-')}\n**Am**:{r.get('Am','-')}\n**Wt**:{r.get('Wt','-')}",
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
    async with ClientSession() as session:
        rows = await fetch_sheet(session)
    if rows is None:
        await interaction.followup.send("❌ APIへのアクセスに失敗しました", ephemeral=True)
        return

    rows = [
        r for r in rows
        if isinstance(r, dict)
        and r.get("曲名")
        and r.get("作曲者")
        and (
            keyword in str(r.get("曲名","")) or
            keyword in str(r.get("作曲者","")) or
            any(keyword in str(r.get(c,"")) for c in ("Sp","Sm","Am","Wt"))
        )
    ]
    
    if not rows:
        await interaction.followup.send("🔍 該当する曲はありません")
        return

    embed = discord.Embed(title="🎵 曲一覧", color=0x5865F2)
    for r in rows[:10]:
        embed.add_field(
            name=f"{STATUS_EMOJI.get(r.get('ステータス'),'❓')} {r['曲名']} / {r['作曲者']}",
            value=f"**Sp**:{r.get('Sp','-')}\n**Sm**:{r.get('Sm','-')}\n**Am**:{r.get('Am','-')}\n**Wt**:{r.get('Wt','-')}",
            inline=False
        )
    embed.set_footer(text=f"凡例:{STATUS_LEGEND}")
    await interaction.followup.send(embed=embed)

# ======================
# /deadline
# ======================
@bot.tree.command(name="deadline", description="自分の作業中・優先作業タスクをDMで確認", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def deadline(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    charter_map = load_charters()
    my_aliases = [name for name, users in charter_map.items() if interaction.user.id in users]

    if not my_aliases:
        await interaction.followup.send("❌ あなたの名義が /list に登録されていません", ephemeral=True)
        return
    
    async with ClientSession() as session:
        rows = await fetch_sheet(session)
    
    if rows is None:
        await interaction.followup.send("❌ APIへのアクセスに失敗しました", ephemeral=True)
        return

    embed = discord.Embed(title="⏰ 担当中のタスク", color=0xFEE75C)
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

        matched_diffs = [diff for diff in ("Sp","Sm","Am","Wt") if any(alias in str(r.get(diff,"")) for alias in my_aliases)]
        if not matched_diffs:
            continue

        found = True
        timestamp = int(target.timestamp())
        embed.add_field(
            name=r.get("曲名","不明"),
            value=f"**担当難易度**:{' / '.join(matched_diffs)}\n**納期**:<t:{timestamp}:R>",
            inline=False
        )

    if not found:
        await interaction.followup.send("📭 現在、担当中のタスクはありません", ephemeral=True)
        return

    try:
        await interaction.user.send(embed=embed)
        await interaction.followup.send("📬 DMに担当中タスクを送信しました", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ DMを送信できませんでした。DM受信設定を確認してください", ephemeral=True)

# ======================
# 納期自動DM
# ======================
@tasks.loop(hours=24)
async def deadline_check():
    async with ClientSession() as session:
        rows = await fetch_sheet(session)
    if rows is None:
        print("Failed to fetch Sheet for deadline check")
        return

    today = datetime.now(timezone.utc).date()
    charters = load_charters()
    notified = load_notified()

    for r in rows:
        if not isinstance(r, dict):
            continue
        status = str(r.get("ステータス","")).strip()
        if status not in ("作業中","優先作業"):
            continue

        date_str = str(r.get("本収録日","")).strip()
        title = r.get("曲名","不明")
        try:
            target = datetime.strptime(date_str, "%Y/%m/%d").date()
        except Exception:
            continue

        diff_map = {}
        for diff in ("Sp","Sm","Am","Wt"):
            cell = str(r.get(diff,"")).strip()
            for name, uid_list in charters.items():
                if name in cell:
                    for uid in uid_list:
                        try:
                            diff_map.setdefault(int(uid), set()).add(diff)
                        except:
                            continue

        for days, tag in ((21,"week3"),(14,"week2")):
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
                    await user.send(f"⏰ 納期通知 ({days}日前)\n{title}\n担当:{' / '.join(diffs)}\n納期:{date_str}")
                    print(f"DM sent to {user} ({uid})")
                except Exception as e:
                    print(f"Failed to send DM to {uid}: {e}")
            notified[key] = today.isoformat()

    save_notified(notified)

# ======================
# Bot起動
# ======================
if __name__ == "__main__":
    bot.run(TOKEN)
