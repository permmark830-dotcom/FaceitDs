# bot.py
# -*- coding: utf-8 -*-
import os, re, math, random, sqlite3, asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "MTU0ODY0OTM0NjI0ODIxMjYxMA.GfhuBT.uHLW1Js3lgd8o0n6yZhDkt6fhLibvn3-1RJjTY")
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
async def check_registered(inter: discord.Interaction):
    p = get_player(inter.user.id)
    if not p:
        await reply(inter, E("❌ Ошибка", "Ты не зарегистрирован. Нажми «Регистрация».", COLOR_RED))
        return None
    return p

async def check_banned(inter: discord.Interaction) -> bool:
    b = get_ban(inter.user.id)
    if b:
        r = b["reason"] or "не указана"
        await reply(inter, E("🚫 Ты забанен", f"Причина: {r}", COLOR_RED))
        return True
    return False

# ================== ГЛАВНОЕ МЕНЮ ==================
def main_menu_embed(inter: discord.Interaction) -> discord.Embed:
    p = get_player(inter.user.id)
    if p:
        total = p["wins"] + p["losses"]
        wr = round(p["wins"]/total*100, 1) if total else 0
        desc = (
            f"👤 **{p['nickname']}** (`{p['game_id']}`)\n"
            f"🏆 ELO: **{p['elo']}** | {rank_for_elo(p['elo'])}\n"
            f"⚔ K/D/A: {p['kills']}/{p['deaths']}/{p['assists']} | WR: {wr}%\n\n"
            f"Выбирай действие кнопками ниже 👇"
        )
    else:
        desc = "Ты ещё не зарегистрирован. Нажми **«Регистрация»**, чтобы начать.\n\nВыбирай действие кнопками ниже 👇"
    return E("🎮 Faceit Standknife", desc, COLOR_GOLD)

class MainMenu(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Регистрация", emoji="📝", style=discord.ButtonStyle.green, row=0)
    async def b_reg(self, inter: discord.Interaction, btn: discord.ui.Button):
        if get_player(inter.user.id):
            await inter.response.send_message("Ты уже зарегистрирован.", ephemeral=True); return
        await inter.response.send_modal(RegisterModal())

    @discord.ui.button(label="Профиль", emoji="👤", style=discord.ButtonStyle.primary, row=0)
    async def b_profile(self, inter: discord.Interaction, btn: discord.ui.Button):
        p = get_player(inter.user.id)
        if not p:
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        total = p["wins"]+p["losses"]; wr = round(p["wins"]/total*100,1) if total else 0
        kd = round(p["kills"]/p["deaths"],2) if p["deaths"] else p["kills"]
        emb = E(f"📊 Профиль {p['nickname']}", color=COLOR_GOLD)
        emb.add_field(name="🎮 ID", value=p["game_id"], inline=True)
        emb.add_field(name="🏆 ELO", value=str(p["elo"]), inline=True)
        emb.add_field(name="🎖 Ранг", value=rank_for_elo(p["elo"]), inline=True)
        emb.add_field(name="⚔ K/D/A", value=f"{p['kills']}/{p['deaths']}/{p['assists']}", inline=True)
        emb.add_field(name="💀 K/D", value=str(kd), inline=True)
        emb.add_field(name="📈 Винрейт", value=f"{wr}% ({p['wins']}W/{p['losses']}L)", inline=True)
        emb.add_field(name="🎯 Матчей", value=str(p["matches"]), inline=True)
        await inter.response.send_message(embed=emb, ephemeral=True)

    @discord.ui.button(label="Топ-10", emoji="🏆", style=discord.ButtonStyle.primary, row=0)
    async def b_top(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM players ORDER BY elo DESC LIMIT 10")
        rows = cur.fetchall()
        if not rows:
            await inter.response.send_message("Пока нет игроков.", ephemeral=True); return
        lines = [f"`#{i}` **{r['nickname']}** — {r['elo']} {rank_for_elo(r['elo'])}" for i,r in enumerate(rows,1)]
        await inter.response.send_message(embed=E("🏆 Топ-10", "\n".join(lines), COLOR_GOLD), ephemeral=True)

    @discord.ui.button(label="Сменить ник", emoji="✏", style=discord.ButtonStyle.secondary, row=1)
    async def b_rename(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not get_player(inter.user.id):
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        await inter.response.send_modal(RenameModal())

    @discord.ui.button(label="Сменить игровой ID", emoji="🆔", style=discord.ButtonStyle.secondary, row=1)
    async def b_myid(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not get_player(inter.user.id):
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        await inter.response.send_modal(MyIdModal())

    @discord.ui.button(label="Создать пати", emoji="🎉", style=discord.ButtonStyle.green, row=2)
    async def b_party_create(self, inter: discord.Interaction, btn: discord.ui.Button):
        p = get_player(inter.user.id)
        if not p:
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        if get_party_of_member(inter.user.id):
            await inter.response.send_message("Ты уже в пати.", ephemeral=True); return
        cur.execute("INSERT INTO parties(owner_id,status,created_at) VALUES(?,?,?)",
                    (inter.user.id,"open",now_iso()))
        pid = cur.lastrowid
        cur.execute("INSERT INTO party_members(party_id,player_id) VALUES(?,?)",(pid,inter.user.id))
        conn.commit()
        emb = party_embed(pid, p["nickname"])
        await inter.response.send_message(embed=emb, view=PartyView(pid, inter.user.id), ephemeral=True)
        try:
            msg = await inter.original_response()
            cur.execute("UPDATE parties SET message_id=?,channel_id=? WHERE id=?",
                        (msg.id,msg.channel.id,pid)); conn.commit()
        except Exception as e: print("party create:", e)

    @discord.ui.button(label="Войти в пати", emoji="📨", style=discord.ButtonStyle.secondary, row=2)
    async def b_party_join(self, inter: discord.Interaction, btn: discord.ui.Button):
        await inter.response.send_modal(JoinPartyModal())

    @discord.ui.button(label="Создать лобби", emoji="🎮", style=discord.ButtonStyle.green, row=3)
    async def b_lobby_create(self, inter: discord.Interaction, btn: discord.ui.Button):
        p = get_player(inter.user.id)
        if not p:
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        if get_lobby_of_player(inter.user.id):
            await inter.response.send_message("Ты уже в лобби.", ephemeral=True); return
        await inter.response.send_message("Выбери режим:", view=ModeSelectView(inter.user.id), ephemeral=True)

    @discord.ui.button(label="Войти в лобби", emoji="🔑", style=discord.ButtonStyle.primary, row=3)
    async def b_lobby_join(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not get_player(inter.user.id):
            await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
        await inter.response.send_modal(LobbyJoinModal())

    @discord.ui.button(label="Список лобби", emoji="📋", style=discord.ButtonStyle.secondary, row=3)
    async def b_lobby_list(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM lobbies WHERE status!='finished' ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        if not rows:
            await inter.response.send_message("Нет открытых лобби.", ephemeral=True); return
        lines = [f"`{r['code']}` — {r['mode']} — {r['status']} ({len(get_lobby_players(r['id']))})" for r in rows]
        await inter.response.send_message(embed=E("🎮 Лобби", "\n".join(lines)), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
    async def b_refresh(self, inter: discord.Interaction, btn: discord.ui.Button):
        await inter.response.edit_message(embed=main_menu_embed(inter), view=MainMenu())

# ================== МОДАЛКИ ==================
class RegisterModal(discord.ui.Modal, title="Регистрация"):
    game_id = discord.ui.TextInput(label="Игровой ID", placeholder="Например: Steve123", max_length=64)
    nickname = discord.ui.TextInput(label="Ник", placeholder="Твой ник в игре", max_length=32)
    async def on_submit(self, inter: discord.Interaction):
        if get_ban(inter.user.id):
            await inter.response.send_message("🚫 Ты забанен.", ephemeral=True); return
        if get_player(inter.user.id):
            await inter.response.send_message("Ты уже зарегистрирован.", ephemeral=True); return
        if get_player_by_gid(self.game_id.value):
            await inter.response.send_message("Этот игровой ID уже занят.", ephemeral=True); return
        try:
            create_player(inter.user.id, self.game_id.value, self.nickname.value)
            await inter.response.send_message(
                embed=E("✅ Регистрация", f"Добро пожаловать, **{self.nickname.value}**!\nELO: 0\nРанг: {rank_for_elo(0)}", COLOR_GREEN),
                ephemeral=True)
        except Exception as e:
            print("reg modal:", e)
            await inter.response.send_message("Ошибка регистрации.", ephemeral=True)

class RenameModal(discord.ui.Modal, title="Смена ника"):
    nick = discord.ui.TextInput(label="Новый ник", max_length=32)
    async def on_submit(self, inter: discord.Interaction):
        cur.execute("UPDATE players SET nickname=? WHERE discord_id=?", (self.nick.value, inter.user.id)); conn.commit()
        await inter.response.send_message(f"✅ Ник изменён на **{self.nick.value}**", ephemeral=True)

class MyIdModal(discord.ui.Modal, title="Смена игрового ID"):
    gid = discord.ui.TextInput(label="Новый игровой ID", max_length=64)
    async def on_submit(self, inter: discord.Interaction):
        if get_player_by_gid(self.gid.value):
            await inter.response.send_message("ID занят.", ephemeral=True); return
        cur.execute("UPDATE players SET game_id=? WHERE discord_id=?", (self.gid.value, inter.user.id)); conn.commit()
        await inter.response.send_message(f"✅ ID изменён на **{self.gid.value}**", ephemeral=True)

class LobbyJoinModal(discord.ui.Modal, title="Вход в лобби"):
    code = discord.ui.TextInput(label="Код лобби (4 цифры)", max_length=8)
    async def on_submit(self, inter: discord.Interaction):
        code = self.code.value.strip()
        lob = get_lobby_by_code(code)
        if not lob or lob["status"]=="finished":
            await inter.response.send_message("Лобби не найдено.", ephemeral=True); return
        if get_lobby_of_player(inter.user.id):
            await inter.response.send_message("Ты уже в лобби.", ephemeral=True); return
        players = get_lobby_players(lob["id"])
        size = team_size(lob["mode"])
        t1 = [p for p in players if p["team"]==1]; t2 = [p for p in players if p["team"]==2]
        if len(t1)>=size and len(t2)>=size:
            await inter.response.send_message("Лобби заполнено.", ephemeral=True); return
        team = 1 if len(t1)<size else 2
        cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,0,0)",
                    (lob["id"], inter.user.id, team)); conn.commit()
        await inter.response.send_message(f"✅ Ты в Team {team} (лобби `{code}`).", ephemeral=True)
        await update_lobby_msg(lob["id"])
        await maybe_start_banning(lob["id"])

class JoinPartyModal(discord.ui.Modal, title="Войти в пати"):
    game_id = discord.ui.TextInput(label="Игровой ID владельца пати", max_length=64)
    async def on_submit(self, inter: discord.Interaction):
        await inter.response.send_message("Владелец должен пригласить тебя через панель пати.", ephemeral=True)

# ================== ПАТИ ==================
def party_embed(party_id:int, owner_nick:str) -> discord.Embed:
    members = get_party_members(party_id)
    lines = []
    for i, m in enumerate(members, 1):
        p = get_player(m)
        if p: lines.append(f"`{i}.` **{p['nickname']}** ({p['elo']} ELO)")
    return E("🎉 Пати", f"Владелец: **{owner_nick}**\nСостав ({len(members)}/5):\n" + "\n".join(lines), COLOR_GREEN)

class PartyInviteModal(discord.ui.Modal, title="Пригласить в пати"):
    game_id = discord.ui.TextInput(label="Игровой ID приглашаемого", max_length=64)
    def __init__(self, party_id:int, owner_id:int):
        super().__init__(); self.party_id = party_id; self.owner_id = owner_id
    async def on_submit(self, inter: discord.Interaction):
        target = get_player_by_gid(self.game_id.value)
        if not target:
            await inter.response.send_message("Игрок не найден.", ephemeral=True); return
        if get_party_of_member(target["discord_id"]):
            await inter.response.send_message("Игрок уже в пати.", ephemeral=True); return
        if len(get_party_members(self.party_id)) >= 5:
            await inter.response.send_message("Пати полна.", ephemeral=True); return
        cur.execute("INSERT INTO party_invites(party_id,inviter_id,invitee_id,status,created_at) VALUES(?,?,?,?,?)",
                    (self.party_id, inter.user.id, target["discord_id"], "pending", now_iso()))
        conn.commit()
        inv_id = cur.lastrowid
        await inter.response.send_message(f"✅ Приглашение отправлено {target['nickname']}.", ephemeral=True)
        try:
            user = await bot.fetch_user(target["discord_id"])
            await user.send(embed=E("📨 Приглашение в пати", f"<@{inter.user.id}> приглашает тебя в пати.", COLOR_BLUE),
                            view=PartyInviteView(inv_id, self.party_id))
        except Exception as e: print("invite dm:", e)

class PartyView(discord.ui.View):
    def __init__(self, party_id:int, owner_id:int):
        super().__init__(timeout=900)
        self.party_id = party_id; self.owner_id = owner_id

    @discord.ui.button(label="Пригласить", emoji="➕", style=discord.ButtonStyle.green, row=0)
    async def inv(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Только владелец.", ephemeral=True); return
        await inter.response.send_modal(PartyInviteModal(self.party_id, self.owner_id))

    @discord.ui.button(label="Покинуть", emoji="🚪", style=discord.ButtonStyle.red, row=0)
    async def leave(self, inter: discord.Interaction, btn: discord.ui.Button):
        party = get_party_of_member(inter.user.id)
        if not party:
            await inter.response.send_message("Ты не в пати.", ephemeral=True); return
        cur.execute("DELETE FROM party_members WHERE party_id=? AND player_id=?", (party["id"], inter.user.id)); conn.commit()
        members = get_party_members(party["id"])
        if not members:
            cur.execute("UPDATE parties SET status='closed' WHERE id=?", (party["id"],)); conn.commit()
        elif party["owner_id"] == inter.user.id and members:
            cur.execute("UPDATE parties SET owner_id=? WHERE id=?", (members[0], party["id"])); conn.commit()
        await inter.response.send_message("Ты покинул пати.", ephemeral=True)

    @discord.ui.button(label="Начать игру", emoji="🎮", style=discord.ButtonStyle.green, row=1)
    async def play(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Только владелец.", ephemeral=True); return
        members = get_party_members(self.party_id)
        if len(members) < 2:
            await inter.response.send_message("Нужно минимум 2 игрока.", ephemeral=True); return
        for m in members:
            if get_lobby_of_player(m):
                await inter.response.send_message("Кто-то из пати уже в лобби.", ephemeral=True); return
        mode = "5v5" if len(members) > 2 else "2v2"
        code = gen_lobby_code()
        cur.execute("INSERT INTO lobbies(code,owner_id,guild_id,mode,status,created_at) VALUES(?,?,?,?,?,?)",
                    (code, self.owner_id, inter.guild_id, mode, "waiting", now_iso()))
        lid = cur.lastrowid
        for m in members:
            cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,?,0)",
                        (lid, m, 1, 1 if m==self.owner_id else 0))
        conn.commit()
        await inter.response.send_message(f"✅ Лобби создано! Код: `{code}`. Режим: {mode}. Все из пати в Team 1.", ephemeral=True)
        try:
            await inter.channel.send(embed=E("🎮 Лобби", f"Код: `{code}`\nХост: <@{self.owner_id}>\nРежим: {mode}", COLOR_GREEN),
                                     view=LobbyView(lid))
            # сохраняем message_id/channel_id
            # берём последнее сообщение
            async for m in inter.channel.history(limit=1):
                cur.execute("UPDATE lobbies SET message_id=?,channel_id=? WHERE id=?", (m.id, m.channel.id, lid)); conn.commit()
        except Exception as e: print("party play send:", e)

    @discord.ui.button(label="Распустить", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def disband(self, inter: discord.Interaction, btn: discord.ui.Button):
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Только владелец.", ephemeral=True); return
        cur.execute("UPDATE parties SET status='closed' WHERE id=?", (self.party_id,))
        cur.execute("DELETE FROM party_members WHERE party_id=?", (self.party_id,))
        conn.commit()
        await inter.response.send_message("🗑 Пати распущено.", ephemeral=True)

class PartyInviteView(discord.ui.View):
    def __init__(self, invite_id:int, party_id:int):
        super().__init__(timeout=600); self.invite_id = invite_id; self.party_id = party_id
    @discord.ui.button(label="Принять", emoji="✅", style=discord.ButtonStyle.green)
    async def accept(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM party_invites WHERE id=?", (self.invite_id,)); inv = cur.fetchone()
        if not inv or inv["status"]!="pending":
            await inter.response.send_message("Приглашение недействительно.", ephemeral=True); return
        if get_party_of_member(inter.user.id):
            await inter.response.send_message("Ты уже в пати.", ephemeral=True); return
        if len(get_party_members(self.party_id)) >= 5:
            await inter.response.send_message("Пати полна.", ephemeral=True); return
        cur.execute("INSERT OR IGNORE INTO party_members(party_id,player_id) VALUES(?,?)", (self.party_id, inter.user.id))
        cur.execute("UPDATE party_invites SET status='accepted' WHERE id=?", (self.invite_id,))
        conn.commit()
        await inter.response.edit_message(content="✅ Ты принял приглашение.", embed=None, view=None)
    @discord.ui.button(label="Отклонить", emoji="❌", style=discord.ButtonStyle.red)
    async def decline(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("UPDATE party_invites SET status='declined' WHERE id=?", (self.invite_id,)); conn.commit()
        await inter.response.edit_message(content="❌ Отклонено.", embed=None, view=None)

# ================== ЛОББИ ==================
def lobby_embed(lid:int) -> discord.Embed:
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: return E("❌", "Лобби не найдено", COLOR_RED)
    players = get_lobby_players(lid)
    size = team_size(lob["mode"])
    def fmt(team):
        s = ""
        for p in players:
            if p["team"]!=team: continue
            pl = get_player(p["player_id"])
            cap = " 👑" if p["captain"] else ""
            s += f"• {pl['nickname'] if pl else p['player_id']}{cap}\n"
        return s or "—"
    t1 = [p for p in players if p["team"]==1]; t2 = [p for p in players if p["team"]==2]
    emb = E(f"🎮 Лобби `{lob['code']}` | {lob['mode']}",
            f"Статус: **{lob['status']}**\nХост: <@{lob['owner_id']}>\n\n"
            f"**Team 1** ({len(t1)}/{size})\n{fmt(1)}\n"
            f"**Team 2** ({len(t2)}/{size})\n{fmt(2)}", COLOR_GOLD)
    if lob["banned_maps"]:
        emb.add_field(name="Забанено", value=lob["banned_maps"], inline=False)
    if lob["final_map"]:
        emb.add_field(name="Карта", value=lob["final_map"], inline=False)
    if lob["status"]=="finished" and lob["score"]:
        emb.add_field(name="Итог", value=f"{lob['score']} | Team {lob['winner_team']}", inline=False)
    return emb

class ModeSelectView(discord.ui.View):
    def __init__(self, owner_id:int):
        super().__init__(timeout=120); self.owner_id = owner_id
    async def _create(self, inter: discord.Interaction, mode:str):
        if get_lobby_of_player(inter.user.id):
            await inter.response.send_message("Ты уже в лобби.", ephemeral=True); return
        code = gen_lobby_code()
        cur.execute("INSERT INTO lobbies(code,owner_id,guild_id,mode,status,created_at) VALUES(?,?,?,?,?,?)",
                    (code, inter.user.id, inter.guild_id, mode, "waiting", now_iso()))
        lid = cur.lastrowid
        cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,?,0)",
                    (lid, inter.user.id, 1, 1))
        conn.commit()
        await inter.response.send_message(embed=lobby_embed(lid), view=LobbyView(lid), ephemeral=True)
        try:
            msg = await inter.original_response()
            cur.execute("UPDATE lobbies SET message_id=?,channel_id=? WHERE id=?", (msg.id, msg.channel.id, lid)); conn.commit()
        except Exception as e: print("mode create msg:", e)
    @discord.ui.button(label="2v2", emoji="⚔", style=discord.ButtonStyle.primary)
    async def m2(self, inter: discord.Interaction, btn: discord.ui.Button): await self._create(inter, "2v2")
    @discord.ui.button(label="5v5", emoji="🛡", style=discord.ButtonStyle.primary)
    async def m5(self, inter: discord.Interaction, btn: discord.ui.Button): await self._create(inter, "5v5")

class LobbyView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=None); self.lid = lid
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

async def _lobby_join(inter: discord.Interaction, lid:int):
    if get_ban(inter.user.id):
        await inter.response.send_message("🚫 Ты забанен.", ephemeral=True); return
    if not get_player(inter.user.id):
        await inter.response.send_message("Сначала зарегистрируйся.", ephemeral=True); return
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob or lob["status"]!="waiting":
        await inter.response.send_message("Лобби недоступно.", ephemeral=True); return
    if get_lobby_of_player(inter.user.id):
        await inter.response.send_message("Ты уже в лобби.", ephemeral=True); return
    players = get_lobby_players(lid); size = team_size(lob["mode"])
    t1 = [p for p in players if p["team"]==1]; t2 = [p for p in players if p["team"]==2]
    if len(t1)>=size and len(t2)>=size:
        await inter.response.send_message("Лобби заполнено.", ephemeral=True); return
    team = 1 if len(t1)<size else 2
    cur.execute("INSERT INTO lobby_players(lobby_id,player_id,team,captain,ready) VALUES(?,?,?,0,0)",
                (lid, inter.user.id, team)); conn.commit()
    await inter.response.send_message(f"✅ Ты в Team {team}.", ephemeral=True)
    await update_lobby_msg(lid)
    await maybe_start_banning(lid)

async def _lobby_leave(inter: discord.Interaction, lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob: await inter.response.send_message("Лобби нет.", ephemeral=True); return
    cur.execute("DELETE FROM lobby_players WHERE lobby_id=? AND player_id=?", (lid, inter.user.id)); conn.commit()
    if lob["owner_id"] == inter.user.id:
        cur.execute("UPDATE lobbies SET status='finished' WHERE id=?", (lid,)); conn.commit()
    await inter.response.send_message("Ты покинул лобби.", ephemeral=True)
    await update_lobby_msg(lid)

async def update_lobby_msg(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    if not lob or not lob["channel_id"] or not lob["message_id"]: return
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        msg = await ch.fetch_message(lob["message_id"])
        await safe_edit(msg, embed=lobby_embed(lid), view=LobbyView(lid))
    except Exception as e: print("update_lobby_msg:", e)

# ================== БАН КАРТ ==================
class MapBanView(discord.ui.View):
    def __init__(self, lid:int, cap_id:int, remaining:List[str]):
        super().__init__(timeout=180); self.lid = lid; self.cap_id = cap_id
        for m in remaining: self.add_item(MapBanButton(lid, cap_id, m))

class MapBanButton(discord.ui.Button):
    def __init__(self, lid:int, cap_id:int, map_name:str):
        super().__init__(label=map_name, style=discord.ButtonStyle.primary)
        self.lid = lid; self.cap_id = cap_id; self.map_name = map_name
    async def callback(self, inter: discord.Interaction):
        cur.execute("SELECT * FROM lobbies WHERE id=?", (self.lid,)); lob = cur.fetchone()
        if not lob or lob["status"]!="banning":
            await inter.response.send_message("Не в фазе бана.", ephemeral=True); return
        order = [x for x in (lob["ban_order"] or "").split(",") if x]
        turn = lob["ban_turn"]
        if turn >= len(order):
            await inter.response.send_message("Бан завершён.", ephemeral=True); return
        if inter.user.id != int(order[turn]):
            await inter.response.send_message("Сейчас не твой ход!", ephemeral=True); return
        banned = [x for x in (lob["banned_maps"] or "").split(",") if x]
        if self.map_name in banned:
            await inter.response.send_message("Карта уже забанена.", ephemeral=True); return
        banned.append(self.map_name)
        remaining = [m for m in MAPS if m not in banned]
        cur.execute("UPDATE lobbies SET banned_maps=?, ban_turn=? WHERE id=?",
                    (",".join(banned), turn+1, self.lid)); conn.commit()
        if len(remaining) <= 1:
            final = remaining[0] if remaining else MAPS[-1]
            cur.execute("UPDATE lobbies SET final_map=?, status='ready' WHERE id=?", (final, self.lid)); conn.commit()
            await inter.response.send_message(f"✅ Забанено: {self.map_name}. Финальная карта: **{final}**", ephemeral=True)
            await update_lobby_msg(self.lid)
            await start_ready_phase(self.lid)
        else:
            cur.execute("SELECT * FROM lobbies WHERE id=?", (self.lid,)); lob2 = cur.fetchone()
            order2 = [x for x in (lob2["ban_order"] or "").split(",") if x]
            t2 = lob2["ban_turn"]
            nxt = order2[t2] if t2 < len(order2) else None
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
    players = get_lobby_players(lid); size = team_size(lob["mode"])
    t1 = [p for p in players if p["team"]==1]; t2 = [p for p in players if p["team"]==2]
    if len(t1) < size or len(t2) < size: return
    def top_elo(tp):
        best = None; be = -1
        for p in tp:
            pl = get_player(p["player_id"])
            if pl and pl["elo"] > be: be = pl["elo"]; best = p["player_id"]
        return best
    c1 = top_elo(t1); c2 = top_elo(t2)
    cur.execute("UPDATE lobby_players SET captain=0 WHERE lobby_id=?", (lid,))
    cur.execute("UPDATE lobby_players SET captain=1 WHERE lobby_id=? AND player_id=?", (lid, c1))
    cur.execute("UPDATE lobby_players SET captain=1 WHERE lobby_id=? AND player_id=?", (lid, c2))
    order = [c1, c2, c1, c2, c1, c2, c1, c2]
    cur.execute("UPDATE lobbies SET status='banning', ban_order=?, ban_turn=0 WHERE id=?",
                (",".join(str(x) for x in order), lid)); conn.commit()
    await update_lobby_msg(lid)
    await send_ban_menu(lid, c1)

# ================== ГОТОВНОСТЬ ==================
class ReadyView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=60); self.lid = lid
    @discord.ui.button(label="Я ГОТОВ", emoji="✅", style=discord.ButtonStyle.green)
    async def ready(self, inter: discord.Interaction, btn: discord.ui.Button):
        cur.execute("SELECT * FROM lobby_players WHERE lobby_id=? AND player_id=?", (self.lid, inter.user.id))
        if not cur.fetchone():
            await inter.response.send_message("Ты не в лобби.", ephemeral=True); return
        cur.execute("UPDATE lobby_players SET ready=1 WHERE lobby_id=? AND player_id=?", (self.lid, inter.user.id)); conn.commit()
        await inter.response.send_message("✅ Готовность подтверждена.", ephemeral=True)
        players = get_lobby_players(self.lid)
        if all(p["ready"] for p in players): await start_match(self.lid)

async def start_ready_phase(lid:int):
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        await ch.send(embed=E("⏱ Подготовка", "20 секунд на подготовку. Затем кнопка «Я ГОТОВ».", COLOR_BLUE))
        await asyncio.sleep(20)
        await ch.send(embed=E("✅ Готовность", "Нажмите кнопку (30 секунд).", COLOR_GREEN), view=ReadyView(lid))
        await asyncio.sleep(30)
        players = get_lobby_players(lid)
        if any(not p["ready"] for p in players):
            lines = []
            for p in players:
                pl = get_player(p["player_id"])
                lines.append(f"{'✅' if p['ready'] else '❌'} {pl['nickname'] if pl else p['player_id']}")
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
    try: cat = await get_or_create_category(guild)
    except Exception as e: print("cat err:", e); return
    t1 = [p["player_id"] for p in players if p["team"]==1]
    t2 = [p["player_id"] for p in players if p["team"]==2]
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for m in t1:
        mem = guild.get_member(m)
        if mem: overwrites[mem] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True)
    for m in t2:
        mem = guild.get_member(m)
        if mem: overwrites[mem] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, send_messages=True)
    code = lob["code"]
    try:
        v1 = await guild.create_voice_channel(f"🔊 Team 1 | {code}", category=cat, overwrites=overwrites)
        v2 = await guild.create_voice_channel(f"🔊 Team 2 | {code}", category=cat, overwrites=overwrites)
        t1c = await guild.create_text_channel(f"💬 team-1-{code}", category=cat, overwrites=overwrites)
        t2c = await guild.create_text_channel(f"💬 team-2-{code}", category=cat, overwrites=overwrites)
    except Exception as e: print("channel create err:", e); return
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
    try:
        await t1c.send(f"Заходите в {v1.mention}")
        await t2c.send(f"Заходите в {v2.mention}")
    except Exception as e: print("msg voice:", e)
    cap1 = next((p["player_id"] for p in players if p["team"]==1 and p["captain"]), None)
    cap2 = next((p["player_id"] for p in players if p["team"]==2 and p["captain"]), None)
    view = ResultView(lid)
    emb = E("🏁 Матч начался!", f"Карта: **{lob['final_map']}**\nКапитаны: <@{cap1}> vs <@{cap2}>\n\nКапитан жмёт «Ввести результат».", COLOR_GOLD)
    try:
        ch = await bot.fetch_channel(lob["channel_id"])
        await ch.send(embed=emb, view=view)
    except Exception as e: print("start match send:", e)
    cur.execute("UPDATE lobbies SET cancel_reason=? WHERE id=?", (f"{v1.id},{v2.id},{t1c.id},{t2c.id}", lid)); conn.commit()

class ResultView(discord.ui.View):
    def __init__(self, lid:int):
        super().__init__(timeout=None); self.lid = lid
    @discord.ui.button(label="Ввести результат", emoji="📝", style=discord.ButtonStyle.green)
    async def result(self, inter: discord.Interaction, btn: discord.ui.Button):
        players = get_lobby_players(self.lid)
        if inter.user.id not in [p["player_id"] for p in players if p["captain"]]:
            await inter.response.send_message("Только капитан.", ephemeral=True); return
        await inter.response.send_message("Отправь результат в ЛС в формате:\n`счёт(10-7)` затем K/D/A каждого игрока построчно: `Ник K D A`. Если забыл скрин — начни с `forgot`.", ephemeral=True)
        try:
            user = await bot.fetch_user(inter.user.id)
            def check(m): return m.author.id == inter.user.id and isinstance(m.channel, discord.DMChannel)
            msg = await bot.wait_for("message", check=check, timeout=300)
            await handle_result_input(self.lid, inter.user.id, msg)
        except asyncio.TimeoutError:
            try: await inter.followup.send("⏱ Время вышло.", ephemeral=True)
            except Exception: pass
        except Exception as e: print("result err:", e)
    @discord.ui.button(label="Отменить матч", emoji="❌", style=discord.ButtonStyle.red)
    async def cancel(self, inter: discord.Interaction, btn: discord.ui.Button):
        players = get_lobby_players(self.lid)
        if inter.user.id not in [p["player_id"] for p in players if p["captain"]]:
            await inter.response.send_message("Только капитан.", ephemeral=True); return
        await inter.response.send_message("Напиши причину отмены в ЛС.", ephemeral=True)
        try:
            def check(m): return m.author.id == inter.user.id and isinstance(m.channel, discord.DMChannel)
            msg = await bot.wait_for("message", check=check, timeout=180)
            reason = msg.content
            cur.execute("UPDATE lobbies SET status='finished', cancel_reason=? WHERE id=?", (reason, self.lid)); conn.commit()
            for p in get_lobby_players(self.lid):
                try:
                    u = await bot.fetch_user(p["player_id"])
                    await u.send(f"❌ Матч отменён. Причина: {reason}")
                except Exception: pass
            await cleanup_match_channels(self.lid)
        except Exception as e: print("cancel err:", e)

async def handle_result_input(lid:int, cap_id:int, msg: discord.Message):
    content = msg.content.strip()
    forgot = content.lower().startswith("forgot")
    if forgot: content = content[6:].strip()
    sm = re.search(r"(\d+)\s*-\s*(\d+)", content)
    if not sm:
        try: await msg.channel.send("❌ Не нашёл счёт (формат 10-7).")
        except Exception: pass
        return
    s1, s2 = int(sm.group(1)), int(sm.group(2))
    rest = content[sm.end():].strip()
    lines = [l.strip() for l in rest.splitlines() if l.strip()]
    cur.execute("SELECT * FROM lobby_players WHERE lobby_id=?", (lid,)); lp_rows = cur.fetchall()
    name_map = {}
    for lp in lp_rows:
        pl = get_player(lp["player_id"])
        if pl: name_map[pl["nickname"].lower()] = lp["player_id"]
    stats = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 4: continue
        try: k,d,a = int(parts[1]), int(parts[2]), int(parts[3])
        except Exception: continue
        pid = name_map.get(parts[0].lower())
        if pid: stats[pid] = (k,d,a)
    winner_team = 1 if s1>s2 else 2
    cur.execute("SELECT * FROM lobbies WHERE id=?", (lid,)); lob = cur.fetchone()
    for lp in lp_rows:
        k,d,a = stats.get(lp["player_id"], (0,0,0))
        cur.execute("UPDATE lobby_players SET kills=?,deaths=?,assists=?,forgot_screenshot=? WHERE lobby_id=? AND player_id=?",
                    (k,d,a, 1 if forgot and lp["player_id"]==cap_id else 0, lid, lp["player_id"]))
    conn.commit()
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
        opp = avg2 if lp["team"]==1 else avg1
        expected = 1/(1+10**((opp - p["elo"])/400))
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
    for cid in (lob["cancel_reason"] or "").split(","):
        cid = cid.strip()
        if not cid.isdigit(): continue
        try:
            ch = await bot.fetch_channel(int(cid)); await ch.delete()
        except Exception as e: print("delete ch:", e)

# ================== СЛЭШ-КОМАНДЫ ==================
@bot.tree.command(name="start", description="Открыть главное меню")
async def cmd_start(inter: discord.Interaction):
    b = get_ban(inter.user.id)
    if b:
        await inter.response.send_message(embed=E("🚫 Ты забанен", f"Причина: {b['reason'] or 'не указана'}", COLOR_RED), ephemeral=True)
        return
    await inter.response.send_message(embed=main_menu_embed(inter), view=MainMenu(), ephemeral=True)

# Дублирующие слэш-команды (для удобства)
@bot.tree.command(name="register", description="Регистрация")
async def cmd_register(inter: discord.Interaction, game_id: str, nickname: str):
    if await check_banned(inter): return
    if get_player(inter.user.id):
        await reply(inter, E("❌","Ты уже зарегистрирован.",COLOR_RED)); return
    if get_player_by_gid(game_id):
        await reply(inter, E("❌","ID занят.",COLOR_RED)); return
    create_player(inter.user.id, game_id, nickname)
    await reply(inter, E("✅ Регистрация", f"Добро пожаловать, **{nickname}**!", COLOR_GREEN))

@bot.tree.command(name="profile", description="Профиль")
async def cmd_profile(inter: discord.Interaction):
    p = get_player(inter.user.id)
    if not p: await reply(inter, E("❌","Не зарегистрирован.",COLOR_RED)); return
    total = p["wins"]+p["losses"]; wr = round(p["wins"]/total*100,1) if total else 0
    emb = E(f"📊 {p['nickname']}", color=COLOR_GOLD)
    emb.add_field(name="ID", value=p["game_id"], inline=True)
    emb.add_field(name="ELO", value=f"{p['elo']} {rank_for_elo(p['elo'])}", inline=True)
    emb.add_field(name="K/D/A", value=f"{p['kills']}/{p['deaths']}/{p['assists']}", inline=True)
    emb.add_field(name="WR", value=f"{wr}%", inline=True)
    await reply(inter, emb)

@bot.tree.command(name="top", description="Топ-10")
async def cmd_top(inter: discord.Interaction):
    cur.execute("SELECT * FROM players ORDER BY elo DESC LIMIT 10")
    rows = cur.fetchall()
    lines = [f"`#{i}` **{r['nickname']}** — {r['elo']} {rank_for_elo(r['elo'])}" for i,r in enumerate(rows,1)]
    await reply(inter, E("🏆 Топ-10", "\n".join(lines) if lines else "Пусто", COLOR_GOLD), ephemeral=False)

# ================== АДМИНКА ==================
@bot.tree.command(name="ban", description="Забанить игрока")
async def cmd_ban(inter: discord.Interaction, user: discord.Member, days: int, reason: Optional[str]="не указана"):
    if not is_admin(inter.user.id): return
    ban_player(user.id, inter.user.id, reason, days)
    await reply(inter, E("🚫 Забанен", f"{user.mention} — {reason} ({days}д)", COLOR_RED))

@bot.tree.command(name="unban", description="Разбанить")
async def cmd_unban(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    unban_player(user.id)
    await reply(inter, E("✅ Разбанен", user.mention, COLOR_GREEN))

@bot.tree.command(name="banlist", description="Список банов")
async def cmd_banlist(inter: discord.Interaction):
    if not is_admin(inter.user.id): return
    cur.execute("SELECT * FROM bans"); rows = cur.fetchall()
    lines = [f"<@{r['discord_id']}> — {r['reason']} — до {r['until'] or 'навсегда'}" for r in rows] or ["Пусто"]
    await reply(inter, E("📋 Баны", "\n".join(lines)))

@bot.tree.command(name="setelo", description="Установить ELO")
async def cmd_setelo(inter: discord.Interaction, user: discord.Member, value: str):
    if not is_admin(inter.user.id): return
    try: elo = int(value)
    except ValueError:
        elo = RANK_NAMES.get(value.lower())
        if elo is None: await reply(inter, E("❌","Укажи число или ранг.")); return
    cur.execute("UPDATE players SET elo=? WHERE discord_id=?", (elo, user.id)); conn.commit()
    await reply(inter, E("✅", f"ELO {user.mention} = {elo}", COLOR_GREEN))

@bot.tree.command(name="setstats", description="Установить K/D/A")
async def cmd_setstats(inter: discord.Interaction, user: discord.Member, k: int, d: int, a: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET kills=?,deaths=?,assists=? WHERE discord_id=?", (k,d,a,user.id)); conn.commit()
    await reply(inter, E("✅","OK",COLOR_GREEN))

@bot.tree.command(name="setwins", description="Установить победы")
async def cmd_setwins(inter: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET wins=? WHERE discord_id=?", (value,user.id)); conn.commit()
    await reply(inter, E("✅","OK",COLOR_GREEN))

@bot.tree.command(name="setlosses", description="Установить поражения")
async def cmd_setlosses(inter: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET losses=? WHERE discord_id=?", (value,user.id)); conn.commit()
    await reply(inter, E("✅","OK",COLOR_GREEN))

@bot.tree.command(name="resetstats", description="Сброс статистики")
async def cmd_resetstats(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    cur.execute("UPDATE players SET elo=0,kills=0,deaths=0,assists=0,wins=0,losses=0,matches=0 WHERE discord_id=?", (user.id,)); conn.commit()
    await reply(inter, E("✅","Сброшено.",COLOR_GREEN))

@bot.tree.command(name="delplayer", description="Удалить игрока")
async def cmd_delplayer(inter: discord.Interaction, user: discord.Member):
    if not is_admin(inter.user.id): return
    cur.execute("DELETE FROM players WHERE discord_id=?", (user.id,)); conn.commit()
    await reply(inter, E("✅","Удалён.",COLOR_RED))

@bot.tree.command(name="adminhelp", description="Помощь по админке")
async def cmd_adminhelp(inter: discord.Interaction):
    if not is_admin(inter.user.id): return
    txt = "/ban /unban /banlist /setelo /setstats /setwins /setlosses /resetstats /delplayer"
    await reply(inter, E("🛠 Админ-команды", txt))

# ================== СЕКРЕТНЫЙ КОД ==================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if message.content.strip() == "!" + SECRET_ADMIN_CODE:
        cur.execute("SELECT value FROM meta WHERE key='secret_used'"); row = cur.fetchone()
        if row and row["value"]=="1":
            try: await message.reply("❌ Код уже использован.")
            except Exception: pass
            return
        cur.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('secret_used','1')"); conn.commit()
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
        print("Slash синхронизированы.")
    except Exception as e: print("sync:", e)
    print(f"Бот запущен: {bot.user}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
