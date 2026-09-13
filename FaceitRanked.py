# bot.py
# -*- coding: utf-8 -*-
import os, re, math, random, sqlite3, asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
SECRET_ADMIN_CODE = "penis148867xindosxyesos"
DB_PATH = "ranked.db"

COLOR_GOLD = 0xFFD700
COLOR_GREEN = 0x00FF88
COLOR_RED = 0xFF4444
COLOR_BLUE = 0x5865F2

MAPS = ["Prison", "Hanami", "Rust", "Dune", "Breeze", "Province", "Sandstone"]

RANKS = [(0,"🥉 Bronze"),(100,"🥈 Silver"),(300,"🥇 Gold"),(600,"💎 Platinum"),
         (1000,"💠 Diamond"),(1500,"👑 Master"),(2000,"🔥 Legend")]
RANK_NAMES = {"bronze":0,"silver":100,"gold":300,"platinum":600,
              "diamond":1000,"master":1500,"legend":2000}

def rank_for_elo(elo:int)->str:
    name = RANKS[0][1]
    for t,r in RANKS:
        if elo >= t: name = r
        else: break
    return name

# ================== БД ==================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def db_init():
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS players(
        discord_id INTEGER PRIMARY KEY, game_id TEXT UNIQUE, nickname TEXT,
        elo INTEGER DEFAULT 0, kills INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        matches INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS parties(
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, channel_id INTEGER,
        message_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT);
    CREATE TABLE IF NOT EXISTS party_members(
        party_id INTEGER, player_id INTEGER, PRIMARY KEY(party_id,player_id));
    CREATE TABLE IF NOT EXISTS party_invites(
        id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER, inviter_id INTEGER,
        invitee_id INTEGER, status TEXT DEFAULT 'pending', created_at TEXT);
    CREATE TABLE IF NOT EXISTS lobbies(
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, owner_id INTEGER,
        guild_id INTEGER, mode TEXT, status TEXT DEFAULT 'waiting',
        message_id INTEGER, channel_id INTEGER, banned_maps TEXT DEFAULT '',
        final_map TEXT, score TEXT, winner_team INTEGER, cancel_reason TEXT,
        ban_order TEXT DEFAULT '', ban_turn INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS lobby_players(
        lobby_id INTEGER, player_id INTEGER, team INTEGER, captain INTEGER DEFAULT 0,
        ready INTEGER DEFAULT 0, kills INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0, screenshot TEXT, forgot_screenshot INTEGER DEFAULT 0,
        PRIMARY KEY(lobby_id,player_id));
    CREATE TABLE IF NOT EXISTS match_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, lobby_code TEXT, mode TEXT, map TEXT,
        score TEXT, winner_team INTEGER, played_at TEXT);
    CREATE TABLE IF NOT EXISTS admins(discord_id INTEGER PRIMARY KEY, added_at TEXT);
    CREATE TABLE IF NOT EXISTS bans(discord_id INTEGER PRIMARY KEY, banned_by INTEGER,
        reason TEXT, until TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
db_init()

def now_iso(): return datetime.now(timezone.utc).isoformat()

def get_player(did:int):
    cur.execute("SELECT * FROM players WHERE discord_id=?", (did,)); return cur.fetchone()
def get_player_by_gid(gid:str):
    cur.execute("SELECT * FROM players WHERE game_id=?", (gid,)); return cur.fetchone()
def create_player(did:int, gid:str, nick:str):
    cur.execute("INSERT INTO players(discord_id,game_id,nickname) VALUES(?,?,?)",(did,gid,nick)); conn.commit()
def is_admin(did:int):
    cur.execute("SELECT 1 FROM admins WHERE discord_id=?", (did,)); return cur.fetchone() is not None
def add_admin(did:int):
    cur.execute("INSERT OR IGNORE INTO admins(discord_id,added_at) VALUES(?,?)",(did,now_iso())); conn.commit()
def get_ban(did:int):
    cur.execute("SELECT * FROM bans WHERE discord_id=?", (did,)); row = cur.fetchone()
    if not row: return None
    if row["until"]:
        try:
            if datetime.fromisoformat(row["until"]) < datetime.now(timezone.utc):
                cur.execute("DELETE FROM bans WHERE discord_id=?", (did,)); conn.commit(); return None
        except Exception as e: print("ban parse:", e)
    return row
def ban_player(did:int, by:int, reason:str, days:int):
    until = (datetime.now(timezone.utc)+timedelta(days=days)).isoformat() if days and days>0 else None
    cur.execute("INSERT OR REPLACE INTO bans(discord_id,banned_by,reason,until,created_at) VALUES(?,?,?,?,?)",
                (did,by,reason,until,now_iso())); conn.commit()
def unban_player(did:int):
    cur.execute("DELETE FROM bans WHERE discord_id=?", (did,)); conn.commit()

def get_party_by_owner(oid:int):
    cur.execute("SELECT * FROM parties WHERE owner_id=? AND status='open'", (oid,)); return cur.fetchone()
def get_party_of_member(pid:int):
    cur.execute("SELECT p.* FROM parties p JOIN party_members m ON m.party_id=p.id WHERE m.player_id=? AND p.status='open'", (pid,)); return cur.fetchone()
def get_party_members(pid:int):
    cur.execute("SELECT player_id FROM party_members WHERE party_id=?", (pid,)); return [r["player_id"] for r in cur.fetchall()]

def gen_lobby_code():
    for _ in range(300):
        c = f"{random.randint(1000,9999)}"
        cur.execute("SELECT 1 FROM lobbies WHERE code=? AND status!='finished'", (c,))
        if not cur.fetchone(): return c
    return f"{random.randint(1000,9999)}"
def get_lobby_by_code(code:str):
    cur.execute("SELECT * FROM lobbies WHERE code=?", (code,)); return cur.fetchone()
def get_lobby_of_player(pid:int):
    cur.execute("SELECT l.* FROM lobbies l JOIN lobby_players lp ON lp.lobby_id=l.id WHERE lp.player_id=? AND l.status!='finished'", (pid,)); return cur.fetchone()
def get_lobby_players(lid:int):
    cur.execute("SELECT * FROM lobby_players WHERE lobby_id=? ORDER BY team, captain DESC", (lid,)); return cur.fetchall()
def team_size(mode:str): return 2 if mode=="2v2" else 5

# ================== БОТ ==================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

def E(title, desc="", color=COLOR_GOLD): return discord.Embed(title=title, description=desc, color=color)

async def safe_edit(msg, **kw):
    if msg is None: return None
    try: return await msg.edit(**kw)
    except Exception as e:
        print("edit err:", e)
        try: return await msg.channel.send(**kw)
        except Exception as e2: print("edit fallback:", e2); return None

async def reply(inter: discord.Interaction, embed: discord.Embed, view=None, ephemeral=True):
    try:
        if inter.response.is_done():
            await inter.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        else:
            await inter.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
    except Exception as e: print("reply err:", e)

# ================== ПРОВЕРКИ ==================
async def check_registered(inter: discord.Interaction) -> Optional[sqlite3.Row]:
    p = get_player(inter.user.id)
    if not p:
        await reply(inter, E("❌ Ошибка", "Ты не зарегистрирован. Используй `/register`.", COLOR_RED))
        return None
    return p

async def check_banned(inter: discord.Interaction) -> bool:
    b = get_ban(inter.user.id)
    if b:
        r = b["reason"] or "не указана"
        await reply(inter, E("🚫 Ты забанен", f"Причина: {r}", COLOR_RED))
        return True
    return False

# ================== РЕГИСТРАЦИЯ / ПРОФИЛЬ ==================
@bot.tree.command(name="register", description="Регистрация в системе")
@app_commands.describe(game_id="Твой игровой ID", nickname="Твой ник")
async def cmd_register(inter: discord.Interaction, game_id: str, nickname: str):
    if await check_banned(inter): return
    if get_player(inter.user.id):
        await reply(inter, E("❌ Ошибка", "Ты уже зарегистрирован.", COLOR_RED)); return
    if get_player_by_gid(game_id):
        await reply(inter, E("❌ Ошибка", "Этот игровой ID уже занят.", COLOR_RED)); return
    try:
        create_player(inter.user.id, game_id, nickname)
        await reply(inter, E("✅ Регистрация", f"Добро пожаловать, **{nickname}**!\nELO: 0\nРанг: {rank_for_elo(0)}", COLOR_GREEN))
    except Exception as e:
        print("register err:", e)
        await reply(inter, E("❌ Ошибка", str(e), COLOR_RED))

@bot.tree.command(name="profile", description="Профиль игрока")
@app_commands.describe(user="Игрок (по умолчанию — ты)")
async def cmd_profile(inter: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or inter.user
    p = get_player(target.id)
    if not p:
        await reply(inter, E("❌", "Игрок не зарегистрирован.", COLOR_RED)); return
    total = p["wins"] + p["losses"]
    wr = round(p["wins"]/total*100, 1) if total else 0
    kd = round(p["kills"]/p["deaths"], 2) if p["deaths"] else p["kills"]
    emb = E(f"📊 Профиль {p['nickname']}", color=COLOR_GOLD)
    emb.add_field(name="🎮 Игровой ID", value=p["game_id"], inline=True)
    emb.add_field(name="🏆 ELO", value=str(p["elo"]), inline=True)
    emb.add_field(name="🎖 Ранг", value=rank_for_elo(p["elo"]), inline=True)
    emb.add_field(name="⚔ K/D/A", value=f"{p['kills']}/{p['deaths']}/{p['assists']}", inline=True)
    emb.add_field(name="💀 K/D", value=str(kd), inline=True)
    emb.add_field(name="📈 Винрейт", value=f"{wr}% ({p['wins']}W/{p['losses']}L)", inline=True)
    emb.add_field(name="🎯 Матчей", value=str(p["matches"]), inline=True)
    await reply(inter, emb, ephemeral=False)

@bot.tree.command(name="top", description="Топ-10 игроков")
async def cmd_top(inter: discord.Interaction):
    cur.execute("SELECT * FROM players ORDER BY elo DESC LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        await reply(inter, E("🏆 Топ", "Пока нет игроков.")); return
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"`#{i}` **{r['nickname']}** — {r['elo']} ELO {rank_for_elo(r['elo'])}")
    await reply(inter, E("🏆 Топ-10", "\n".join(lines), COLOR_GOLD), ephemeral=False)

@bot.tree.command(name="rename", description="Сменить ник")
async def cmd_rename(inter: discord.Interaction, new_nick: str):
    if await check_banned(inter): return
    p = get_player(inter.user.id)
    if not p: await reply(inter, E("❌", "Ты не зарегистрирован.", COLOR_RED)); return
    cur.execute("UPDATE players SET nickname=? WHERE discord_id=?", (new_nick, inter.user.id)); conn.commit()
    await reply(inter, E("✅", f"Ник изменён на **{new_nick}**", COLOR_GREEN))

@bot.tree.command(name="myid", description="Сменить игровой ID")
async def cmd_myid(inter: discord.Interaction, new_id: str):
    if await check_banned(inter): return
    p = get_player(inter.user.id)
    if not p: await reply(inter, E("❌", "Ты не зарегистрирован.", COLOR_RED)); return
    if get_player_by_gid(new_id):
        await reply(inter, E("❌", "ID уже занят.", COLOR_RED)); return
    cur.execute("UPDATE players SET game_id=? WHERE discord_id=?", (new_id, inter.user.id)); conn.commit()
    await reply(inter, E("✅", f"Игровой ID изменён на **{new_id}**", COLOR_GREEN))

# ================== ПАТИ ==================
def party_embed(party_id:int, owner_nick:str) -> discord.Embed:
    members = get_party_members(party_id)
    lines = []
    for i, m in enumerate(members, 1):
        p = get_player(m)
        if p: lines.append(f"`{i}.` **{p['nickname']}** ({p['elo']} ELO)")
    emb = E("🎉 Пати", f"Владелец: **{owner_nick}**\nСостав ({len(members)}/5):\n" + "\n".join(lines), COLOR_GREEN)
    return emb

class PartyView(discord.ui.View):
    def __init__(self, party_id:int, owner_id:int):
        super().__init__(timeout=None)
        self.party_id = party_id
        self.owner_id = owner_id

    @discord.ui.button(label="Начать игру", emoji="🎮", style=discord.ButtonStyle.green)
    async def play(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Только владелец пати может начать игру.", ephemeral=True); return
        members = get_party_members(self.party_id)
        # распределяем в лобби — создаём лобби 2v2 или 5v5 по количеству
        if len(members) < 2:
            await inter.response.send_message("Нужно минимум 2 игрока.", ephemeral=True); return
        mode = "5v5" if len(members) > 2 else "2v2"
        # проверим что никто не в лобби
        for m in members:
            if get_lobby_of_player(m):
                await inter.response.send_message("Кто-то из пати уже в лобби.", ephemeral=True); return
        # создаём лобби
        code = gen_lobby_code()
        cur.execute("INSERT INTO lobbies(code,owner_id,guild_id,mode,status,created_at) VALUES(?,?,?,?,?,?)",
                    (code, self.owner_id, inter.guild_id, mode, "waiting", now_iso()))
        lid = cur.lastrowid
        for m in members:
            cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,?,0)",
                        (lid, m, 1, 1 if m==self.owner_id else 0))
        conn.commit()
        await inter.response.send_message(f"✅ Лобби создано! Код: `{code}`. Заходите другие игроки через `/lobby join {code}`.", ephemeral=True)
        # рассылаем в канал
        try:
            ch = inter.channel
            await ch.send(embed=E("🎮 Лобби создано", f"Код: `{code}`\nРежим: {mode}\nХост: <@{self.owner_id}>", COLOR_GREEN))
        except Exception as e: print("party play send:", e)

    @discord.ui.button(label="Распустить", emoji="🗑", style=discord.ButtonStyle.red)
    async def disband(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Только владелец.", ephemeral=True); return
        cur.execute("UPDATE parties SET status='closed' WHERE id=?", (self.party_id,))
        cur.execute("DELETE FROM party_members WHERE party_id=?", (self.party_id,))
        conn.commit()
        await inter.response.send_message("🗑 Пати распущено.", ephemeral=True)

@bot.tree.command(name="party", description="Управление пати")
@app_commands.describe(action="create/invite/leave/disband/play", game_id="ID для invite")
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="invite", value="invite"),
    app_commands.Choice(name="leave", value="leave"),
    app_commands.Choice(name="disband", value="disband"),
    app_commands.Choice(name="play", value="play"),
])
async def cmd_party(inter: discord.Interaction, action: str, game_id: Optional[str] = None):
    if await check_banned(inter): return
    p = get_player(inter.user.id)
    if not p: await reply(inter, E("❌", "Сначала `/register`.", COLOR_RED)); return

    if action == "create":
        if get_party_of_member(inter.user.id):
            await reply(inter, E("❌", "Ты уже в пати.", COLOR_RED)); return
        cur.execute("INSERT INTO parties(owner_id,status,created_at) VALUES(?,?,?)",
                    (inter.user.id, "open", now_iso()))
        pid = cur.lastrowid
        cur.execute("INSERT INTO party_members(party_id,player_id) VALUES(?,?)", (pid, inter.user.id))
        conn.commit()
        emb = party_embed(pid, p["nickname"])
        await inter.response.send_message(embed=emb, view=PartyView(pid, inter.user.id))
        try:
            msg = await inter.original_response()
            cur.execute("UPDATE parties SET message_id=?, channel_id=? WHERE id=?",
                        (msg.id, msg.channel.id, pid)); conn.commit()
        except Exception as e: print("party msg:", e)

    elif action == "invite":
        if not game_id:
            await reply(inter, E("❌", "Укажи game_id.")); return
        party = get_party_of_member(inter.user.id)
        if not party:
            await reply(inter, E("❌", "Ты не в пати.")); return
        if party["owner_id"] != inter.user.id:
            await reply(inter, E("❌", "Приглашать может только владелец.")); return
        target = get_player_by_gid(game_id)
        if not target:
            await reply(inter, E("❌", "Игрок не найден.")); return
        if get_party_of_member(target["discord_id"]):
            await reply(inter, E("❌", "Игрок уже в пати.")); return
        if len(get_party_members(party["id"])) >= 5:
            await reply(inter, E("❌", "Пати полна (5).")); return
        # создаём приглашение
        cur.execute("INSERT INTO party_invites(party_id,inviter_id,invitee_id,status,created_at) VALUES(?,?,?,?,?)",
                    (party["id"], inter.user.id, target["discord_id"], "pending", now_iso()))
        conn.commit()
        inv_id = cur.lastrowid
        await reply(inter, E("✅", f"Приглашение отправлено {target['nickname']}."))
        try:
            user = await bot.fetch_user(target["discord_id"])
            view = PartyInviteView(inv_id, party["id"])
            emb = E("📨 Приглашение в пати", f"<@{inter.user.id}> приглашает тебя в пати.", COLOR_BLUE)
            await user.send(embed=emb, view=view)
        except Exception as e: print("invite dm:", e)

    elif action == "leave":
        party = get_party_of_member(inter.user.id)
        if not party:
            await reply(inter, E("❌", "Ты не в пати.")); return
        cur.execute("DELETE FROM party_members WHERE party_id=? AND player_id=?", (party["id"], inter.user.id))
        conn.commit()
        members = get_party_members(party["id"])
        if not members:
            cur.execute("UPDATE parties SET status='closed' WHERE id=?", (party["id"],)); conn.commit()
        elif party["owner_id"] == inter.user.id and members:
            # передаём владение первому
            cur.execute("UPDATE parties SET owner_id=? WHERE id=?", (members[0], party["id"])); conn.commit()
        await reply(inter, E("✅", "Ты вышел из пати."))

    elif action == "disband":
        party = get_party_of_member(inter.user.id)
        if not party:
            await reply(inter, E("❌", "Ты не в пати.")); return
        if party["owner_id"] != inter.user.id:
            await reply(inter, E("❌", "Только владелец.")); return
        cur.execute("UPDATE parties SET status='closed' WHERE id=?", (party["id"],))
        cur.execute("DELETE FROM party_members WHERE party_id=?", (party["id"],))
        conn.commit()
        await reply(inter, E("🗑", "Пати распущено."))

    elif action == "play":
        party = get_party_of_member(inter.user.id)
        if not party:
            await reply(inter, E("❌", "Ты не в пати.")); return
        if party["owner_id"] != inter.user.id:
            await reply(inter, E("❌", "Только владелец.")); return
        members = get_party_members(party["id"])
        if len(members) < 2:
            await reply(inter, E("❌", "Нужно минимум 2 игрока.")); return
        for m in members:
            if get_lobby_of_player(m):
                await reply(inter, E("❌", "Кто-то из пати уже в лобби.")); return
        mode = "5v5" if len(members) > 2 else "2v2"
        code = gen_lobby_code()
        cur.execute("INSERT INTO lobbies(code,owner_id,guild_id,mode,status,created_at) VALUES(?,?,?,?,?,?)",
                    (code, inter.user.id, inter.guild_id, mode, "waiting", now_iso()))
        lid = cur.lastrowid
        for m in members:
            cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,?,0)",
                        (lid, m, 1, 1 if m==inter.user.id else 0))
        conn.commit()
        await reply(inter, E("🎮 Лобби создано", f"Код: `{code}`\nРежим: {mode}", COLOR_GREEN))
        try:
            await inter.channel.send(embed=E("🎮 Лобби", f"Код: `{code}`\nХост: <@{inter.user.id}>", COLOR_GREEN))
        except Exception as e: print("party play send:", e)

class PartyInviteView(discord.ui.View):
    def __init__(self, invite_id:int, party_id:int):
        super().__init__(timeout=300)
        self.invite_id = invite_id
        self.party_id = party_id
    @discord.ui.button(label="Принять", emoji="✅", style=discord.ButtonStyle.green)
    async def accept(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM party_invites WHERE id=?", (self.invite_id,))
        inv = cur.fetchone()
        if not inv or inv["status"]!="pending":
            await inter.response.send_message("Приглашение недействительно.", ephemeral=True); return
        if get_party_of_member(inter.user.id):
            await inter.response.send_message("Ты уже в пати.", ephemeral=True); return
        members = get_party_members(self.party_id)
        if len(members) >= 5:
            await inter.response.send_message("Пати полна.", ephemeral=True); return
        cur.execute("INSERT OR IGNORE INTO party_members(party_id,player_id) VALUES(?,?)", (self.party_id, inter.user.id))
        cur.execute("UPDATE party_invites SET status='accepted' WHERE id=?", (self.invite_id,))
        conn.commit()
        await inter.response.edit_message(content="✅ Ты принял приглашение.", embed=None, view=None)
    @discord.ui.button(label="Отклонить", emoji="❌", style=discord.ButtonStyle.red)
    async def decline(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("UPDATE party_invites SET status='declined' WHERE id=?", (self.invite_id,))
        conn.commit()
        await inter.response.edit_message(content="❌ Ты отклонил приглашение.", embed=None, view=None)

# ================== ЛОББИ ==================
def lobby_embed(lid:int) -> discord.Embed:
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,))
    lob = cur.fetchone()
    players = get_lobby_players(lid)
    t1 = [p for p in players if p["team"]==1]
    t2 = [p for p in players if p["team"]==2]
    size = team_size(lob["mode"])
    def fmt(team, lst):
        s = ""
        for p in lst:
            pl = get_player(p["player_id"])
            cap = " 👑" if p["captain"] else ""
            nick = pl["nickname"] if pl else f"ID{p['player_id']}"
            s += f"• {nick}{cap}\n"
        return s or "—"
    emb = E(f"🎮 Лобби `{lob['code']}` | {lob['mode']}",
            f"Статус: **{lob['status']}**\nХост: <@{lob['owner_id']}>\n\n"
            f"**Team 1** ({len(t1)}/{size})\n{fmt(1,t1)}\n"
            f"**Team 2** ({len(t2)}/{size})\n{fmt(2,t2)}", COLOR_GOLD)
    if lob["banned_maps"]:
        emb.add_field(name="Забанено", value=lob["banned_maps"] or "—", inline=False)
    if lob["final_map"]:
        emb.add_field(name="Финальная карта", value=lob["final_map"], inline=False)
    return emb

class LobbyView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=None)
        self.lid = lid
    @discord.ui.button(label="Занять слот", emoji="➕", style=discord.ButtonStyle.green)
    async def join_btn(self, inter: discord.Interaction, btn: discord.ui.Button):
        await _lobby_join(inter, self.lid)
    @discord.ui.button(label="Покинуть", emoji="🚪", style=discord.ButtonStyle.red)
    async def leave_btn(self, inter: discord.Interaction, btn: discord.ui.Button):
        await _lobby_leave(inter, self.lid)
    @discord.ui.button(label="Удалить", emoji="🗑", style=discord.ButtonStyle.danger)
    async def del_btn(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM lobbies WHERE id=?", (self.lid,)); lob = cur.fetchone()
        if not lob: await inter.response.send_message("Лобби не найдено.", ephemeral=True); return
        if inter.user.id != lob["owner_id"]:
            await inter.response.send_message("Только хост.", ephemeral=True); return
        cur.execute("UPDATE lobbies SET status='finished' WHERE id=?", (self.lid,))
        cur.execute("DELETE FROM lobby_players WHERE lobby_id=?", (self.lid,))
        conn.commit()
        await inter.response.send_message("🗑 Лобби удалено.", ephemeral=True)
        # редактируем сообщение
        try:
            ch = await bot.fetch_channel(lob["channel_id"])
            msg = await ch.fetch_message(lob["message_id"])
            await msg.edit(embed=E("🗑 Лобби удалено", "Хост закрыл лобби.", COLOR_RED), view=None)
        except Exception as e: print("lobby del edit:", e)

async def _lobby_join(inter: discord.Interaction, lid:int):
    if get_ban(inter.user.id):
        await inter.response.send_message("🚫 Ты забанен.", ephemeral=True); return
    p = get_player(inter.user.id)
    if not p:
        await inter.response.send_message("Сначала `/register`.", ephemeral=True); return
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob or lob["status"]!="waiting":
        await inter.response.send_message("Лобби недоступно.", ephemeral=True); return
    if get_lobby_of_player(inter.user.id):
        await inter.response.send_message("Ты уже в лобби.", ephemeral=True); return
    players = get_lobby_players(lid)
    size = team_size(lob["mode"])
    t1 = [p for p in players if p["team"]==1]
    t2 = [p for p in players if p["team"]==2]
    team = 1 if len(t1)<size else 2
    if len(t1)>=size and len(t2)>=size:
        await inter.response.send_message("Лобби заполнено.", ephemeral=True); return
    cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,0,0)",
                (lid, inter.user.id, team))
    conn.commit()
    await inter.response.send_message(f"✅ Ты в Team {team}.", ephemeral=True)
    await update_lobby_msg(lid)
    await maybe_start_banning(lid)

async def _lobby_leave(inter: discord.Interaction, lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: await inter.response.send_message("Лобби нет.", ephemeral=True); return
    cur.execute("DELETE FROM lobby_players WHERE lobby_id=? AND player_id=?", (lid, inter.user.id))
    conn.commit()
    if lob["owner_id"] == inter.user.id:
        cur.execute("UPDATE lobbies SET status='finished' WHERE id=?", (lid,)); conn.commit()
        await inter.response.send_message("Ты покинул лобби (хост).", ephemeral=True)
    else:
        await inter.response.send_message("Ты покинул лобби.", ephemeral=True)
    await update_lobby_msg(lid)

async def update_lobby_msg(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: return
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        msg = await ch.fetch_message(lob["message_id"])
        await safe_edit(msg, embed=lobby_embed(lid), view=LobbyView(lid))
    except Exception as e: print("update_lobby_msg:", e)

@bot.tree.command(name="lobby", description="Управление лобби")
@app_commands.describe(action="create/join/leave/list", mode="2v2 или 5v5", code="Код лобби")
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="join", value="join"),
    app_commands.Choice(name="leave", value="leave"),
    app_commands.Choice(name="list", value="list"),
])
async def cmd_lobby(inter: discord.Interaction, action: str, mode: Optional[str]=None, code: Optional[str]=None):
    if await check_banned(inter): return
    p = get_player(inter.user.id)
    if not p: await reply(inter, E("❌","Сначала `/register`.",COLOR_RED)); return

    if action == "create":
        if mode not in ("2v2","5v5"):
            await reply(inter, E("❌","Укажи mode: 2v2 или 5v5.")); return
        if get_lobby_of_player(inter.user.id):
            await reply(inter, E("❌","Ты уже в лобби.")); return
        c = gen_lobby_code()
        cur.execute("INSERT INTO lobbies(code,owner_id,guild_id,mode,status,created_at) VALUES(?,?,?,?,?,?)",
                    (c, inter.user.id, inter.guild_id, mode, "waiting", now_iso()))
        lid = cur.lastrowid
        cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,?,0)",
                    (lid, inter.user.id, 1, 1))
        conn.commit()
        emb = lobby_embed(lid)
        await inter.response.send_message(embed=emb, view=LobbyView(lid))
        try:
            msg = await inter.original_response()
            cur.execute("UPDATE lobbies SET message_id=?, channel_id=? WHERE id=?", (msg.id, msg.channel.id, lid))
            conn.commit()
        except Exception as e: print("lobby create msg:", e)

    elif action == "join":
        if not code:
            await reply(inter, E("❌","Укажи code.")); return
        lob = get_lobby_by_code(code)
        if not lob:
            await reply(inter, E("❌","Лобби не найдено.")); return
        await _lobby_join(inter, lob["id"])

    elif action == "leave":
        lob = get_lobby_of_player(inter.user.id)
        if not lob:
            await reply(inter, E("❌","Ты не в лобби.")); return
        await _lobby_leave(inter, lob["id"])

    elif action == "list":
        cur.execute("SELECT * FROM lobbies WHERE status!='finished' ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        if not rows:
            await reply(inter, E("🎮 Лобби","Нет открытых лобби.")); return
        lines = []
        for r in rows:
            cnt = len(get_lobby_players(r["id"]))
            lines.append(f"`{r['code']}` — {r['mode']} — {r['status']} ({cnt} игроков)")
        await reply(inter, E("🎮 Лобби", "\n".join(lines)))

# ================== БАН КАРТ ==================
class MapBanView(discord.ui.View):
    def __init__(self, lid:int, cap_id:int, remaining:List[str]):
        super().__init__(timeout=180)
        self.lid = lid
        self.cap_id = cap_id
        for m in remaining:
            self.add_item(MapBanButton(lid, cap_id, m))

class MapBanButton(discord.ui.Button):
    def __init__(self, lid:int, cap_id:int, map_name:str):
        super().__init__(label=map_name, style=discord.ButtonStyle.primary)
        self.lid = lid; self.cap_id = cap_id; self.map_name = map_name
    async def callback(self, inter: discord.Interaction):
        cur.execute("SELECT * FROM lobbies WHERE id=?", (self.lid,)); lob = cur.fetchone()
        if not lob or lob["status"]!="banning":
            await inter.response.send_message("Не в фазе бана.", ephemeral=True); return
        players = get_lobby_players(self.lid)
        caps = [p["player_id"] for p in players if p["captain"]]
        # проверка очереди
        order = (lob["ban_order"] or "").split(",")
        order = [x for x in order if x]
        turn = lob["ban_turn"]
        if turn >= len(order):
            await inter.response.send_message("Бан завершён.", ephemeral=True); return
        if inter.user.id != int(order[turn]):
            await inter.response.send_message("Сейчас не твой ход!", ephemeral=True); return
        # бан
        banned = (lob["banned_maps"] or "").split(",")
        banned = [x for x in banned if x]
        if self.map_name in banned:
            await inter.response.send_message("Карта уже забанена.", ephemeral=True); return
        banned.append(self.map_name)
        remaining = [m for m in MAPS if m not in banned]
        cur.execute("UPDATE lobbies SET banned_maps=?, ban_turn=? WHERE id=?",
                    (",".join(banned), turn+1, self.lid))
        conn.commit()
        if len(remaining) <= 1:
            final = remaining[0] if remaining else MAPS[-1]
            cur.execute("UPDATE lobbies SET final_map=?, status='ready' WHERE id=?", (final, self.lid))
            conn.commit()
            await inter.response.send_message(f"✅ Забанено: {self.map_name}. Финальная карта: **{final}**", ephemeral=True)
            await update_lobby_msg(self.lid)
            await start_ready_phase(self.lid)
        else:
            cur.execute("SELECT * FROM lobbies WHERE id=?", (self.lid,)); lob2 = cur.fetchone()
            order2 = (lob2["ban_order"] or "").split(",")
            turn2 = lob2["ban_turn"]
            nxt = order2[turn2] if turn2 < len(order2) else None
            await inter.response.send_message(f"✅ Забанено: {self.map_name}. Ход: <@{nxt}>" if nxt else f"✅ Забанено: {self.map_name}.", ephemeral=True)
            await update_lobby_msg(self.lid)
            if nxt: await send_ban_menu(self.lid, int(nxt))

async def send_ban_menu(lid:int, cap_id:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    banned = [x for x in (lob["banned_maps"] or "").split(",") if x]
    remaining = [m for m in MAPS if m not in banned]
    try:
        user = await bot.fetch_user(cap_id)
        await user.send(embed=E("🗺 Бан карт", f"Твой ход. Осталось: {', '.join(remaining)}", COLOR_BLUE),
                        view=MapBanView(lid, cap_id, remaining))
    except Exception as e: print("send_ban_menu:", e)

async def maybe_start_banning(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob or lob["status"]!="waiting": return
    players = get_lobby_players(lid)
    size = team_size(lob["mode"])
    t1 = [p for p in players if p["team"]==1]
    t2 = [p for p in players if p["team"]==2]
    if len(t1) < size or len(t2) < size: return
    # назначаем капитанов — макс ELO в каждой команде
    def top_elo(team_players):
        best = None; best_elo = -1
        for p in team_players:
            pl = get_player(p["player_id"])
            if pl and pl["elo"] > best_elo:
                best_elo = pl["elo"]; best = p["player_id"]
        return best
    c1 = top_elo(t1); c2 = top_elo(t2)
    cur.execute("UPDATE lobby_players SET captain=0 WHERE lobby_id=?", (lid,))
    cur.execute("UPDATE lobby_players SET captain=1 WHERE lobby_id=? AND player_id=?", (lid, c1))
    cur.execute("UPDATE lobby_players SET captain=1 WHERE lobby_id=? AND player_id=?", (lid, c2))
    order = [c1, c2, c1, c2, c1, c2, c1, c2]
    cur.execute("UPDATE lobbies SET status='banning', ban_order=?, ban_turn=0 WHERE id=?",
                (",".join(str(x) for x in order), lid))
    conn.commit()
    await update_lobby_msg(lid)
    await send_ban_menu(lid, c1)

# ================== ГОТОВНОСТЬ ==================
class ReadyView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=60)
        self.lid = lid
    @discord.ui.button(label="Я ГОТОВ", emoji="✅", style=discord.ButtonStyle.green)
    async def ready(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM lobby_players WHERE lobby_id=? AND player_id=?", (self.lid, inter.user.id))
        lp = cur.fetchone()
        if not lp:
            await inter.response.send_message("Ты не в лобби.", ephemeral=True); return
        cur.execute("UPDATE lobby_players SET ready=1 WHERE lobby_id=? AND player_id=?", (self.lid, inter.user.id))
        conn.commit()
        await inter.response.send_message("✅ Готовность подтверждена.", ephemeral=True)
        # проверка
        players = get_lobby_players(self.lid)
        if all(p["ready"] for p in players):
            await start_match(self.lid)
        else:
            await show_ready_status(self.lid)

async def show_ready_status(lid:int):
    players = get_lobby_players(lid)
    lines = []
    for p in players:
        pl = get_player(p["player_id"])
        mark = "✅" if p["ready"] else "❌"
        lines.append(f"{mark} {pl['nickname'] if pl else p['player_id']}")
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: return
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        msg = await ch.fetch_message(lob["message_id"])
        emb = lobby_embed(lid)
        emb.add_field(name="Готовность", value="\n".join(lines), inline=False)
        await safe_edit(msg, embed=emb, view=LobbyView(lid))
    except Exception as e: print("show_ready_status:", e)

async def start_ready_phase(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        await ch.send(embed=E("⏱ Подготовка", "20 секунд на подготовку. Затем нажмите кнопку «Я ГОТОВ».", COLOR_BLUE))
        await asyncio.sleep(20)
        await ch.send(embed=E("✅ Готовность", "Нажмите кнопку ниже (30 секунд).", COLOR_GREEN), view=ReadyView(lid))
        await asyncio.sleep(30)
        players = get_lobby_players(lid)
        not_ready = [p for p in players if not p["ready"]]
        if not_ready:
            lines = []
            for p in players:
                pl = get_player(p["player_id"])
                mark = "✅" if p["ready"] else "❌"
                lines.append(f"{mark} {pl['nickname'] if pl else p['player_id']}")
            cur.execute("UPDATE lobbies SET status='finished' WHERE id=?", (lid,)); conn.commit()
            await ch.send(embed=E("❌ Матч отменён", "Не все подтвердили готовность:\n" + "\n".join(lines), COLOR_RED))
    except Exception as e: print("start_ready_phase:", e)

# ================== МАТЧ / КАНАЛЫ ==================
async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    for c in guild.categories:
        if c.name == "🎮 Faceit Matches": return c
    return await guild.create_category("🎮 Faceit Matches")

async def start_match(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: return
    cur.execute("UPDATE lobbies SET status='playing' WHERE id=?", (lid,)); conn.commit()
    players = get_lobby_players(lid)
    guild = bot.get_guild(lob["guild_id"])
    if not guild:
        try: guild = await bot.fetch_guild(lob["guild_id"])
        except Exception as e: print("guild fetch:", e); return
    try:
        cat = await get_or_create_category(guild)
    except Exception as e:
        print("cat err:", e); return
    # создаём каналы
    t1 = [p["player_id"] for p in players if p["team"]==1]
    t2 = [p["player_id"] for p in players if p["team"]==2]
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for m in t1:
        member = guild.get_member(m)
        if member: overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True)
    for m in t2:
        member = guild.get_member(m)
        if member: overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True)
    code = lob["code"]
    try:
        v1 = await guild.create_voice_channel(f"🔊 Team 1 | {code}", category=cat, overwrites=overwrites)
        v2 = await guild.create_voice_channel(f"🔊 Team 2 | {code}", category=cat, overwrites=overwrites)
        t1c = await guild.create_text_channel(f"💬 team-1-{code}", category=cat, overwrites=overwrites)
        t2c = await guild.create_text_channel(f"💬 team-2-{code}", category=cat, overwrites=overwrites)
    except Exception as e:
        print("channel create err:", e); return
    # перемещаем
    for m in t1:
        mem = guild.get_member(m)
        if mem and mem.voice:
            try: await mem.move_to(v1)
            except Exception as e: print("move:", e)
    for m in t2:
        mem = guild.get_member(m)
        if mem and mem.voice:
            try: await mem.move_to(v2)
            except Exception as e: print("move:", e)
    # сообщения
    try:
        await t1c.send(f"Заходите в {v1.mention}")
        await t2c.send(f"Заходите в {v2.mention}")
    except Exception as e: print("msg voice:", e)
    # панель результата
    cap1 = next((p["player_id"] for p in players if p["team"]==1 and p["captain"]), None)
    cap2 = next((p["player_id"] for p in players if p["team"]==2 and p["captain"]), None)
    view = ResultView(lid)
    emb = E("🏁 Матч начался!", f"Карта: **{lob['final_map']}**\nКапитаны: <@{cap1}> vs <@{cap2}>\n\nПосле матча капитан должен нажать кнопку «Ввести результат».", COLOR_GOLD)
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        await ch.send(embed=emb, view=view)
    except Exception as e: print("start match send:", e)
    # сохраняем каналы
    cur.execute("UPDATE lobbies SET cancel_reason=? WHERE id=?", (f"{v1.id},{v2.id},{t1c.id},{t2c.id}", lid))
    conn.commit()

class ResultView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=None)
        self.lid = lid
    @discord.ui.button(label="Ввести результат", emoji="📝", style=discord.ButtonStyle.green)
    async def result(self, inter: discord.Interaction, btn: discord.ui.Button):
        players = get_lobby_players(self.lid)
        cap_ids = [p["player_id"] for p in players if p["captain"]]
        if inter.user.id not in cap_ids:
            await inter.response.send_message("Только капитан.", ephemeral=True); return
        await inter.response.send_message(
            "Отправь результат одним сообщением:\n"
            "`счёт(10-7) K/D/A_игрок1 K/D/A_игрок2 ...`\n"
            "Или прикрепи скрин + тот же текст.\n"
            "Если забыл скрин — напиши `forgot` в начале.",
            ephemeral=True)
        # ждём в личке
        try:
            user = await bot.fetch_user(inter.user.id)
            def check(m):
                return m.author.id == inter.user.id and isinstance(m.channel, discord.DMChannel)
            msg = await bot.wait_for("message", check=check, timeout=300)
            await handle_result_input(self.lid, inter.user.id, msg)
        except asyncio.TimeoutError:
            await inter.followup.send("⏱ Время вышло.", ephemeral=True)
        except Exception as e:
            print("result err:", e)
    @discord.ui.button(label="Отменить матч", emoji="❌", style=discord.ButtonStyle.red)
    async def cancel(self, inter: discord.Interaction, btn: discord.ui.Button):
        players = get_lobby_players(self.lid)
        cap_ids = [p["player_id"] for p in players if p["captain"]]
        if inter.user.id not in cap_ids:
            await inter.response.send_message("Только капитан.", ephemeral=True); return
        await inter.response.send_message("Напиши причину отмены в ЛС.", ephemeral=True)
        try:
            user = await bot.fetch_user(inter.user.id)
            def check(m): return m.author.id == inter.user.id and isinstance(m.channel, discord.DMChannel)
            msg = await bot.wait_for("message", check=check, timeout=180)
            reason = msg.content
            cur.execute("UPDATE lobbies SET status='finished', cancel_reason=? WHERE id=?", (reason, self.lid)); conn.commit()
            players = get_lobby_players(self.lid)
            for p in players:
                try:
                    u = await bot.fetch_user(p["player_id"])
                    await u.send(f"❌ Матч отменён. Причина: {reason}")
                except Exception: pass
            await cleanup_match_channels(self.lid)
        except Exception as e: print("cancel err:", e)

async def handle_result_input(lid:int, cap_id:int, msg: discord.Message):
    content = msg.content.strip()
    forgot = content.lower().startswith("forgot")
    if forgot:
        content = content[6:].strip()
    # ищем счёт
    score_match = re.search(r"(\d+)\s*-\s*(\d+)", content)
    if not score_match:
        try: await msg.channel.send("❌ Не нашёл счёт (формат 10-7).")
        except Exception: pass
        return
    s1, s2 = int(score_match.group(1)), int(score_match.group(2))
    # парсим строки после счёта
    rest = content[score_match.end():].strip()
    lines = [l.strip() for l in rest.splitlines() if l.strip()]
    stats: Dict[int, Tuple[int,int,int]] = {}
    cur.execute("SELECT * FROM lobby_players WHERE lobby_id=?", (lid,))
    lp_rows = cur.fetchall()
    name_map = {}
    for lp in lp_rows:
        pl = get_player(lp["player_id"])
        if pl: name_map[pl["nickname"].lower()] = lp["player_id"]
    for line in lines:
        parts = line.split()
        if len(parts) < 4: continue
        nick = parts[0].lower()
        try: k,d,a = int(parts[1]), int(parts[2]), int(parts[3])
        except Exception: continue
        pid = name_map.get(nick)
        if pid: stats[pid] = (k,d,a)
    # определяем победителя
    winner_team = 1 if s1>s2 else 2
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    # сохраняем статы
    for lp in lp_rows:
        k,d,a = stats.get(lp["player_id"], (0,0,0))
        cur.execute("UPDATE lobby_players SET kills=?,deaths=?,assists=?,forgot_screenshot=? WHERE lobby_id=? AND player_id=?",
                    (k,d,a, 1 if forgot and lp["player_id"]==cap_id else 0, lid, lp["player_id"]))
    conn.commit()
    # ELO
    teams = {1:[], 2:[]}
    for lp in lp_rows: teams[lp["team"]].append(lp["player_id"])
    def avg_elo(ids):
        vals = [get_player(i)["elo"] for i in ids if get_player(i)]
        return sum(vals)/len(vals) if vals else 0
    avg1, avg2 = avg_elo(teams[1]), avg_elo(teams[2])
    K = 32
    for lp in lp_rows:
        p = get_player(lp["player_id"])
        if not p: continue
        opp_avg = avg2 if lp["team"]==1 else avg1
        expected = 1/(1+10**((opp_avg - p["elo"])/400))
        actual = 1 if lp["team"]==winner_team else 0
        k,d,a = stats.get(lp["player_id"], (0,0,0))
        delta = K*(actual-expected) + (k + a/2 - d)/10
        if forgot and lp["player_id"]==cap_id: delta -= 10
        new_elo = int(round(p["elo"] + delta))
        wins = p["wins"] + (1 if actual==1 else 0)
        losses = p["losses"] + (1 if actual==0 else 0)
        cur.execute("""UPDATE players SET elo=?, kills=kills+?, deaths=deaths+?, assists=assists+?,
                       wins=?, losses=?, matches=matches+1 WHERE discord_id=?""",
                    (new_elo, k, d, a, wins, losses, lp["player_id"]))
    cur.execute("UPDATE lobbies SET status='finished', score=?, winner_team=? WHERE id=?",
                (f"{s1}-{s2}", winner_team, lid))
    cur.execute("INSERT INTO match_history(lobby_code,mode,map,score,winner_team,played_at) VALUES(?,?,?,?,?,?)",
                (lob["code"], lob["mode"], lob["final_map"], f"{s1}-{s2}", winner_team, now_iso()))
    conn.commit()
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        await ch.send(embed=E("🏆 Матч завершён", f"Победила Team {winner_team}\nСчёт: {s1}-{s2}\nКарта: {lob['final_map']}", COLOR_GREEN))
    except Exception as e: print("result announce:", e)
    await asyncio.sleep(30)
    await cleanup_match_channels(lid)

async def cleanup_match_channels(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: return
    ids = (lob["cancel_reason"] or "").split(",")
    for cid in ids:
        if not cid.strip().isdigit(): continue
        try:
            ch = await bot.fetch_channel(int(cid))
            await ch.delete()
        except Exception as e: print("delete ch:", e)

# ================== АДМИНКА ==================
@bot.tree.command(name="ban", description="Забанить игрока")
async def cmd_ban(inter: discord.Interaction, user: discord.Member, days: int, reason: Optional[str]="не указана"):
    if not is_admin(inter.user.id): return
    ban_player(user.id, inter.user.id, reason, days)
    await reply(inter, E("🚫 Забанен", f"{user.mention} — {reason} ({days}д)", COLOR_RED), ephemeral=False)

@bot.tree.command(name="unban", description="Разбанить")
async def cmd_unban(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    unban_player(user.id)
    await reply(inter, E("✅ Разбанен", user.mention, COLOR_GREEN), ephemeral=False)

@bot.tree.command(name="banlist", description="Список банов")
async def cmd_banlist(inter: discord.Interaction):
    if not is_admin(inter.user.id): return
    cur.execute("SELECT * FROM bans")
    rows = cur.fetchall()
    if not rows:
        await reply(inter, E("📋 Баны", "Нет банов.")); return
    lines = []
    for r in rows:
        until = r["until"] or "навсегда"
        lines.append(f"<@{r['discord_id']}> — {r['reason']} — до {until}")
    await reply(inter, E("📋 Баны", "\n".join(lines)))

@bot.tree.command(name="setelo", description="Установить ELO")
async def cmd_setelo(inter: discord.Interaction, user: discord.Member, value: str):
    if not is_admin(inter.user.id): return
    p = get_player(user.id)
    if not p: await reply(inter, E("❌","Игрок не найден.")); return
    try:
        elo = int(value)
    except ValueError:
        elo = RANK_NAMES.get(value.lower())
        if elo is None:
            await reply(inter, E("❌","Укажи число или ранг.")); return
    cur.execute("UPDATE players SET elo=? WHERE discord_id=?", (elo, user.id)); conn.commit()
    await reply(inter, E("✅", f"ELO {user.mention} = {elo}", COLOR_GREEN))

@bot.tree.command(name="setstats", description="Установить K/D/A")
async def cmd_setstats(inter: discord.Interaction, user: discord.Member, k: int, d: int, a: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET kills=?,deaths=?,assists=? WHERE discord_id=?", (k,d,a,user.id)); conn.commit()
    await reply(inter, E("✅","Статы обновлены.",COLOR_GREEN))

@bot.tree.command(name="setwins", description="Установить победы")
async def cmd_setwins(inter: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET wins=? WHERE discord_id=?", (value,user.id)); conn.commit()
    await reply(inter, E("✅","Wins обновлены.",COLOR_GREEN))

@bot.tree.command(name="setlosses", description="Установить поражения")
async def cmd_setlosses(inter: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET losses=? WHERE discord_id=?", (value,user.id)); conn.commit()
    await reply(inter, E("✅","Losses обновлены.",COLOR_GREEN))

@bot.tree.command(name="resetstats", description="Сбросить статистику")
async def cmd_resetstats(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    cur.execute("""UPDATE players SET elo=0,kills=0,deaths=0,assists=0,wins=0,losses=0,matches=0
                   WHERE discord_id=?""", (user.id,)); conn.commit()
    await reply(inter, E("✅","Статистика сброшена.",COLOR_GREEN))

@bot.tree.command(name="delplayer", description="Удалить игрока")
async def cmd_delplayer(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    cur.execute("DELETE FROM players WHERE discord_id=?", (user.id,)); conn.commit()
    await reply(inter, E("✅","Игрок удалён.",COLOR_RED))

@bot.tree.command(name="adminhelp", description="Помощь по админке")
async def cmd_adminhelp(inter: discord.Interaction):
    if not is_admin(inter.user.id): return
    txt = (
        "/ban <user> <days> [reason]\n/unban <user>\n/banlist\n"
        "/setelo <user> <число|ранг>\n/setstats <user> K D A\n"
        "/setwins <user> N\n/setlosses <user> N\n/resetstats <user>\n/delplayer <user>"
    )
    await reply(inter, E("🛠 Админ-команды", txt))

# ================== СЕКРЕТНЫЙ КОД ==================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if message.content.strip() == "!" + SECRET_ADMIN_CODE:
        cur.execute("SELECT value FROM meta WHERE key='secret_used'")
        row = cur.fetchone()
        if row and row["value"]=="1":
            try: await message.reply("❌ Код уже использован.")
            except Exception: pass
            return
        cur.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('secret_used','1')")
        conn.commit()
        add_admin(message.author.id)
        try: await message.reply("✅ Ты теперь админ.")
        except Exception: pass
        if OWNER_ID:
            try:
                owner = await bot.fetch_user(OWNER_ID)
                await owner.send(f"Новый админ: {message.author} (ID {message.author.id})")
            except Exception as e: print("owner notify:", e)
    await bot.process_commands(message)

# ================== СТАРТ ==================
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        print("Slash-команды синхронизированы.")
    except Exception as e:
        print("tree sync err:", e)
    print(f"Бот запущен: {bot.user}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)