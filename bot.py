import discord
from discord.ext import commands
import datetime
import asyncio
import os
import re
import json
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

def get_prefix(bot, message):
    return commands.when_mentioned_or(": ", ":")(bot, message)

bot = commands.Bot(command_prefix=get_prefix, intents=intents, case_insensitive=True)
bot.remove_command("help")

HARDCODED_OWNER_ID = 1512527848970256441

EMBED_COLOR = 0x2B2D31

user_message_timestamps = {}
vm_locked_users = set()
shadowed_users = set()

# ==================== PERSISTENT DATABASE ====================
DATA_FILE = "levels.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "guilds": {}, "stat_roles": {}, "status_roles": {}, "authorized_users": []}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            if "guilds" not in data:
                data["guilds"] = {}
            if "users" not in data:
                data["users"] = {}
            if "stat_roles" not in data:
                data["stat_roles"] = {}
            if "status_roles" not in data:
                data["status_roles"] = {}
            if "authorized_users" not in data:
                data["authorized_users"] = []
            return data
    except Exception:
        return {"users": {}, "guilds": {}, "stat_roles": {}, "status_roles": {}, "authorized_users": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

db_data = load_data()

def get_guild_settings(guild_id):
    g_key = str(guild_id)
    if g_key not in db_data["guilds"]:
        db_data["guilds"][g_key] = {
            "is_enabled": False,
            "max_level": 10,
            "level_msg": "Good job {user}! You are now Level {level} in {guild}!",
            "roles": {}
        }
    else:
        if "is_enabled" not in db_data["guilds"][g_key]:
            db_data["guilds"][g_key]["is_enabled"] = False
        if "max_level" not in db_data["guilds"][g_key]:
            db_data["guilds"][g_key]["max_level"] = 10
        if "level_msg" not in db_data["guilds"][g_key]:
            db_data["guilds"][g_key]["level_msg"] = "Good job {user}! You are now Level {level} in {guild}!"
        if "roles" not in db_data["guilds"][g_key]:
            db_data["guilds"][g_key]["roles"] = {}
    return db_data["guilds"][g_key]

def get_user_xp(guild_id, user_id):
    g_key = str(guild_id)
    u_key = str(user_id)
    if g_key not in db_data["users"]:
        db_data["users"][g_key] = {}
    if u_key not in db_data["users"][g_key]:
        db_data["users"][g_key][u_key] = {"xp": 0.0, "level": 0}
    return db_data["users"][g_key][u_key]

def calculate_level(xp, max_lvl):
    if xp <= 0:
        return 0
    target_max_xp = 1000 if max_lvl <= 10 else 10000
    current_lvl = 0
    for lvl in range(1, max_lvl + 1):
        required_xp_for_lvl = target_max_xp * ((lvl / max_lvl) ** 1.5)
        if xp >= required_xp_for_lvl:
            current_lvl = lvl
        else:
            break
    return min(current_lvl, max_lvl)

def is_strict_owner(ctx):
    if ctx.author.id == HARDCODED_OWNER_ID:
        return True
    if ctx.guild and ctx.guild.owner_id == ctx.author.id:
        return True
    return False

def is_authorized(ctx):
    if is_strict_owner(ctx):
        return True
    return ctx.author.id in db_data.get("authorized_users", [])

async def missing_permission(ctx, permission_name):
    embed = discord.Embed(
        title="Permission Denied",
        description=f"You are missing the required permission: `{permission_name}`",
        color=EMBED_COLOR
    )
    await ctx.reply(embed=embed, mention_author=False)

def is_voice_message(message):
    if getattr(message.flags, "voice", False):
        return True
    for att in message.attachments:
        name = (att.filename or "").lower()
        ctype = (att.content_type or "").lower()
        if "audio" in ctype or name.endswith((".ogg", ".mp3", ".wav", ".m4a")):
            return True
    return False

async def send_and_delete_warning(channel, author_mention):
    try:
        warning = await channel.send(f"Please do not spam! {author_mention}")
        await asyncio.sleep(3)
        await warning.delete()
    except Exception:
        pass

def clean_mentions(content):
    content = re.sub(r"<@!?\d+>", "", content)
    content = content.replace("@everyone", "").replace("@here", "")
    return content.strip()

def create_status_embed(text: str, title: str = None):
    embed = discord.Embed(description=text, color=EMBED_COLOR)
    if title:
        embed.title = title
    return embed

def resolve_role(guild, role_input: str):
    role_input_clean = role_input.strip()

    if role_input_clean.startswith("<@&") and role_input_clean.endswith(">"):
        role_id = re.sub(r"\D", "", role_input_clean)
        if role_id.isdigit():
            role = guild.get_role(int(role_id))
            if role:
                return role

    if role_input_clean.isdigit():
        role = guild.get_role(int(role_input_clean))
        if role:
            return role

    return discord.utils.find(lambda r: r.name.lower() == role_input_clean.lower(), guild.roles)

def format_level_message(template: str, member: discord.Member, new_lvl: int, xp: float, guild: discord.Guild, role_mention: str):
    text = template
    text = re.sub(r'\{@user\}', member.mention, text, flags=re.IGNORECASE)
    text = re.sub(r'\{user\}|\{username\}', member.display_name, text, flags=re.IGNORECASE)
    text = re.sub(r'\{level\}', str(new_lvl), text, flags=re.IGNORECASE)
    text = re.sub(r'\{xp\}', f"{int(xp)}", text, flags=re.IGNORECASE)
    text = re.sub(r'\{guild\}', guild.name, text, flags=re.IGNORECASE)
    text = re.sub(r'\{role\}', role_mention, text, flags=re.IGNORECASE)
    return text

async def process_xp_gain(guild, member, channel, amount_to_add: float):
    guild_config = get_guild_settings(guild.id)
    if not guild_config.get("is_enabled", False):
        return False

    u_data = get_user_xp(guild.id, member.id)
    u_data["xp"] += amount_to_add
    
    old_lvl = u_data["level"]
    max_lvl = guild_config.get("max_level", 10)
    new_lvl = calculate_level(u_data["xp"], max_lvl)

    if new_lvl > old_lvl:
        u_data["level"] = new_lvl
        save_data(db_data)

        reward_roles = guild_config.get("roles", {})
        awarded_role = None

        for lvl_str, role_id in sorted(reward_roles.items(), key=lambda x: int(x[0])):
            if int(lvl_str) <= new_lvl:
                reward_role = guild.get_role(role_id)
                if reward_role and reward_role not in member.roles:
                    try:
                        await member.add_roles(reward_role, reason=f"Reached Level {new_lvl}")
                        if int(lvl_str) == new_lvl:
                            awarded_role = reward_role
                    except Exception:
                        pass

        custom_msg = guild_config.get(
            "level_msg", 
            "Good job {user}! You are now Level {level} in {guild}!"
        )

        formatted_msg = format_level_message(
            template=custom_msg,
            member=member,
            new_lvl=new_lvl,
            xp=u_data["xp"],
            guild=guild,
            role_mention=awarded_role.mention if awarded_role else "None"
        )

        try:
            await channel.send(formatted_msg)
        except Exception:
            pass
        return True
    else:
        save_data(db_data)
        return False

@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user.name} (ID: {bot.user.id})")
    print("Bot is ready.")

# ==================== STATUS ROLE EVENT HANDLER ====================
@bot.event
async def on_presence_update(before, after):
    if after.bot or not after.guild:
        return

    g_key = str(after.guild.id)
    u_key = str(after.id)
    stat_config = db_data.get("stat_roles", {}).get(g_key)

    if not stat_config:
        return

    required_text = stat_config.get("text", "").lower()
    role_id = stat_config.get("role_id")
    target_role = after.guild.get_role(role_id)

    if not target_role or not required_text:
        return

    # Check if user has required text in custom status
    has_text_in_status = False
    for act in after.activities:
        if isinstance(act, discord.CustomActivity) and act.name:
            if required_text in act.name.lower():
                has_text_in_status = True
                break

    if "status_roles" not in db_data:
        db_data["status_roles"] = {}
    if g_key not in db_data["status_roles"]:
        db_data["status_roles"][g_key] = []

    tracked_users = db_data["status_roles"][g_key]

    try:
        # Case 1: User added status text -> Grant role & log in JSON
        if has_text_in_status:
            if target_role not in after.roles:
                await after.add_roles(target_role, reason=f"Matched status requirement: '{required_text}'")
            if u_key not in tracked_users:
                tracked_users.append(u_key)
                save_data(db_data)

        # Case 2: User removed status text -> Take role away & remove from JSON
        elif not has_text_in_status:
            if u_key in tracked_users:
                if target_role in after.roles:
                    await after.remove_roles(target_role, reason=f"Removed status requirement: '{required_text}'")
                tracked_users.remove(u_key)
                save_data(db_data)
    except Exception:
        pass

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if message.author.id in shadowed_users:
        try:
            if message.content:
                cleaned = clean_mentions(message.content)
                if cleaned:
                    await message.reply(
                        cleaned,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none()
                    )
        except Exception:
            pass

    if message.author.id in vm_locked_users and is_voice_message(message):
        try:
            await message.delete()
            warn = await message.channel.send(f"{message.author.mention} you are VM locked.")
            await asyncio.sleep(3)
            await warn.delete()
        except Exception:
            pass
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    key = (message.channel.id, message.author.id)

    if key not in user_message_timestamps:
        user_message_timestamps[key] = []

    user_message_timestamps[key] = [
        t for t in user_message_timestamps[key]
        if (now - t).total_seconds() <= 10.0
    ]
    user_message_timestamps[key].append(now)

    if len(user_message_timestamps[key]) >= 8:
        user_message_timestamps[key] = []
        to_delete = []
        try:
            async for msg in message.channel.history(limit=50):
                if msg.author.id == message.author.id:
                    to_delete.append(msg)
                    if len(to_delete) == 14:
                        break
        except Exception:
            pass

        if to_delete:
            asyncio.create_task(message.channel.delete_messages(to_delete))
        asyncio.create_task(send_and_delete_warning(message.channel, message.author.mention))

    if not message.content.startswith(":"):
        g_config = get_guild_settings(message.guild.id)
        max_lvl = g_config.get("max_level", 10)
        xp_per_msg = 10.0 / max_lvl
        await process_xp_gain(message.guild, message.author, message.channel, xp_per_msg)

    await bot.process_commands(message)

# ==================== BITCH COMMAND GROUP (:bitch) ====================

@bot.group(name="bitch", invoke_without_command=True)
async def bitch_group(ctx):
    if not is_strict_owner(ctx):
        await ctx.send("you are not owner.")
        return
    embed = create_status_embed("Usage:\n`:bitch add @user`\n`:bitch remove @user`\n`:bitch list`")
    await ctx.reply(embed=embed, mention_author=False)

@bitch_group.command(name="add")
async def bitch_add(ctx, member: discord.Member = None):
    if not is_strict_owner(ctx):
        await ctx.send("you are not owner.")
        return

    if member is None:
        embed = create_status_embed("Please mention a user to add.\nUsage: `:bitch add @user`")
        await ctx.reply(embed=embed, mention_author=False)
        return

    if member.id in db_data.get("authorized_users", []):
        embed = create_status_embed(f"**{member.display_name}** is already authorized.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    if "authorized_users" not in db_data:
        db_data["authorized_users"] = []

    db_data["authorized_users"].append(member.id)
    save_data(db_data)

    embed = create_status_embed(f"Granted full bot permission access to **{member.display_name}**.")
    await ctx.reply(embed=embed, mention_author=False)

@bitch_group.command(name="remove")
async def bitch_remove(ctx, member: discord.Member = None):
    if not is_strict_owner(ctx):
        await ctx.send("you are not owner.")
        return

    if member is None:
        embed = create_status_embed("Please mention a user to remove.\nUsage: `:bitch remove @user`")
        await ctx.reply(embed=embed, mention_author=False)
        return

    if member.id not in db_data.get("authorized_users", []):
        embed = create_status_embed(f"**{member.display_name}** is not in the authorized list.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    db_data["authorized_users"].remove(member.id)
    save_data(db_data)

    embed = create_status_embed(f"Removed bot permission access from **{member.display_name}**.")
    await ctx.reply(embed=embed, mention_author=False)

@bitch_group.command(name="list")
async def bitch_list(ctx):
    if not is_strict_owner(ctx):
        await ctx.send("you are not owner.")
        return

    users = db_data.get("authorized_users", [])
    if not users:
        embed = create_status_embed("No extra authorized users configured.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    user_mentions = [f"• <@{uid}> (`{uid}`)" for uid in users]
    embed = create_status_embed("\n".join(user_mentions), title="Authorized Users")
    await ctx.reply(embed=embed, mention_author=False)

# ==================== STATUS ROLE COMMAND (:stat) ====================

@bot.command(name="stat")
async def stat(ctx):
    if not is_authorized(ctx):
        await ctx.send("you are not owner.")
        return

    def check(m):
        return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

    embed1 = create_status_embed("What text should members put in their status/bio to receive the role?")
    await ctx.reply(embed=embed1, mention_author=False)

    try:
        text_msg = await bot.wait_for("message", check=check, timeout=60.0)
    except asyncio.TimeoutError:
        await ctx.send("Timed out. Status role setup cancelled.")
        return

    required_text = text_msg.content.strip()

    embed2 = create_status_embed("What role should be automatically granted to members with that status?")
    await ctx.reply(embed=embed2, mention_author=False)

    try:
        role_msg = await bot.wait_for("message", check=check, timeout=60.0)
    except asyncio.TimeoutError:
        await ctx.send("Timed out. Status role setup cancelled.")
        return

    target_role = resolve_role(ctx.guild, role_msg.content)

    if not target_role:
        embed_err = create_status_embed("Could not find that role. Setup cancelled.")
        await ctx.reply(embed=embed_err, mention_author=False)
        return

    g_key = str(ctx.guild.id)
    if "stat_roles" not in db_data:
        db_data["stat_roles"] = {}

    db_data["stat_roles"][g_key] = {
        "text": required_text,
        "role_id": target_role.id
    }
    save_data(db_data)

    embed_done = create_status_embed(
        f"**Status Role Configured!**\n\n"
        f"• Required Status Text: `{required_text}`\n"
        f"• Auto Granted Role: {target_role.mention}\n\n"
        f"Anyone who adds `{required_text}` to their status will automatically get {target_role.mention}."
    )
    await ctx.reply(embed=embed_done, mention_author=False)

# ==================== XP MANAGEMENT COMMAND GROUP (:xp) ====================

@bot.group(name="xp", invoke_without_command=True)
async def xp_group(ctx):
    embed = discord.Embed(
        title="XP Management Commands",
        description=(
            "• `:xp add <amount> [user]`\nAdd specified amount of XP to a user.\n\n"
            "• `:xp reset <user>`\nReset all XP and level progress for a user."
        ),
        color=EMBED_COLOR
    )
    await ctx.reply(embed=embed, mention_author=False)

@xp_group.command(name="add")
async def xp_add(ctx, amount: float = None, member: discord.Member = None):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    g_config = get_guild_settings(ctx.guild.id)
    if not g_config.get("is_enabled", False):
        embed = discord.Embed(
            title="System Disabled",
            description="Leveling system is not configured for this server. Run `:level setup <max_level>` first.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    if amount is None or amount <= 0:
        embed = discord.Embed(
            title="Invalid Amount",
            description="Please specify a valid positive XP amount.\nUsage: `:xp add <amount> [user]`",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    target = member or ctx.author
    await process_xp_gain(ctx.guild, target, ctx.channel, amount)

@xp_group.command(name="reset")
async def xp_reset(ctx, member: discord.Member = None):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    if member is None:
        embed = discord.Embed(
            title="Missing User",
            description="Please mention a user to reset their XP.\nUsage: `:xp reset @user`",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    g_key = str(ctx.guild.id)
    u_key = str(member.id)

    if g_key in db_data["users"] and u_key in db_data["users"][g_key]:
        db_data["users"][g_key][u_key] = {"xp": 0.0, "level": 0}
        save_data(db_data)

    embed = discord.Embed(
        title="XP Reset",
        description=f"Successfully reset all levels and XP for {member.mention}.",
        color=EMBED_COLOR
    )
    await ctx.reply(embed=embed, mention_author=False)

# ==================== HELP MENU ====================
HELP_CATEGORIES = {
    "moderation": {
        "title": "Moderation Commands",
        "desc": "Commands for moderating members, channels, and roles.",
        "cmds": "ban, r put, r sync",
        "count": "3 commands • 1 groups"
    },
    "level": {
        "title": "Level Commands",
        "desc": "Commands for level tracking, setups, custom texts, and reward roles.",
        "cmds": "level, level setup, level msg, level r, level config, xp add, xp reset, level sync",
        "count": "8 commands • 2 groups"
    },
    "utility": {
        "title": "Utility Commands",
        "desc": "General utility and helper commands.",
        "cmds": "help",
        "count": "1 commands • 0 groups"
    },
    "owner": {
        "title": "Owner Commands",
        "desc": "Exclusive owner utility commands.",
        "cmds": "stat, shadow, nuke, vm lock, vm unlock, bitch add, bitch remove, bitch list",
        "count": "8 commands • 2 groups"
    }
}

class HelpSelect(discord.ui.Select):
    def __init__(self, author):
        self.author = author
        options = [
            discord.SelectOption(label="Moderation", value="moderation", description="Moderation commands for roles and members."),
            discord.SelectOption(label="Leveling", value="level", description="Setup level system, custom message templates, and level roles."),
            discord.SelectOption(label="Utility", value="utility", description="General utility commands."),
            discord.SelectOption(label="Owner", value="owner", description="Management commands for bot owners.")
        ]
        super().__init__(placeholder="Select a category ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This help menu isn't for you!", ephemeral=True)
            return

        cat_key = self.values[0]
        data = HELP_CATEGORIES.get(cat_key)

        embed = discord.Embed(
            title=data["title"],
            description=f"> {data['desc']}\n\n```\n{data['cmds']}\n```",
            color=EMBED_COLOR
        )
        embed.set_author(name=self.author.name, icon_url=self.author.display_avatar.url)
        embed.set_footer(text=data["count"])

        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.message.edit(embed=embed)

class HelpView(discord.ui.View):
    def __init__(self, author, timeout=60.0):
        super().__init__(timeout=timeout)
        self.add_item(HelpSelect(author))

@bot.command(name="help")
async def help_cmd(ctx):
    author = ctx.author

    embed = discord.Embed(
        description=(
            "> Parameters with\n"
            "> `[...]` are optional\n"
            "> and\n"
            "> `<...>` means they're\n"
            "> required.\n\n\n"
            "> Select a category from the dropdown menu below to view available commands."
        ),
        color=EMBED_COLOR
    )
    embed.set_author(name=author.name, icon_url=author.display_avatar.url)
    if bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    view = HelpView(author=author)
    await ctx.reply(embed=embed, view=view, mention_author=False)

# ==================== LEVEL COMMAND GROUP (:level) ====================

def get_level_usage_embed():
    embed = discord.Embed(
        title="Level Commands Usage",
        description="Configure and manage the leveling system for your server.",
        color=EMBED_COLOR
    )
    
    embed.add_field(
        name="Commands",
        value=(
            "• `:level setup <max_level>`\n"
            "Enable and set maximum allowed level (1-100).\n\n"
            "• `:level msg <text>`\n"
            "Set custom level-up message template.\n\n"
            "• `:level r <level> <role>`\n"
            "Assign a reward role for reaching a level.\n\n"
            "• `:level sync`\n"
            "Retroactively grant roles to members who hit required levels.\n\n"
            "• `:level config`\n"
            "View all current server level settings."
        ),
        inline=False
    )
    
    embed.add_field(
        name="Dynamic Placeholders for `:level msg`",
        value=(
            "`{user}` — Plain name (Sin)\n"
            "`{@user}` — Mentions user (@Sin)\n"
            "`{level}` — Reached level (10)\n"
            "`{xp}` — Total user XP\n"
            "`{guild}` — Server name\n"
            "`{role}` — Awarded role"
        ),
        inline=False
    )

    embed.set_footer(text="Leveling System")
    return embed

@bot.group(name="level", invoke_without_command=True)
async def level_group(ctx, member: discord.Member = None):
    g_config = get_guild_settings(ctx.guild.id)
    if not g_config.get("is_enabled", False):
        embed = discord.Embed(
            title="System Disabled",
            description="Leveling system is not configured for this server. An administrator must run `:level setup <max_level>` first.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    target = member or ctx.author
    u_data = get_user_xp(ctx.guild.id, target.id)

    max_lvl = g_config['max_level']
    curr_lvl = u_data['level']
    curr_xp = u_data['xp']

    embed = discord.Embed(
        title=f"Level Profile — {target.display_name}",
        color=EMBED_COLOR
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Current Level", value=f"`{curr_lvl}` / `{max_lvl}`", inline=True)
    embed.add_field(name="Total XP", value=f"`{int(curr_xp)}` XP", inline=True)
    
    progress = min(100, int((curr_lvl / max_lvl) * 100)) if max_lvl > 0 else 0
    bar_filled = int(progress / 10)
    bar_empty = 10 - bar_filled
    progress_bar = "=" * bar_filled + "-" * bar_empty
    embed.add_field(name="Progress", value=f"`[{progress_bar}]` `{progress}%`", inline=False)
    
    await ctx.reply(embed=embed, mention_author=False)

@level_group.command(name="setup")
async def level_setup(ctx, max_level: int = None):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    if max_level is None:
        await ctx.reply(embed=get_level_usage_embed(), mention_author=False)
        return

    if max_level < 1 or max_level > 100:
        embed = discord.Embed(
            title="Invalid Max Level",
            description="The max level must be set between **1** and **100**.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    g_config = get_guild_settings(ctx.guild.id)
    g_config["max_level"] = max_level
    g_config["is_enabled"] = True
    save_data(db_data)

    embed = discord.Embed(
        title="Level Setup Complete",
        description=f"Server leveling system is now **enabled** with a limit of **Level {max_level}**.",
        color=EMBED_COLOR
    )
    await ctx.reply(embed=embed, mention_author=False)

@level_group.command(name="msg")
async def level_msg(ctx, *, custom_text: str = None):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    g_config = get_guild_settings(ctx.guild.id)
    if not g_config.get("is_enabled", False):
        embed = discord.Embed(
            title="System Disabled",
            description="Run `:level setup <max_level>` to enable the leveling system before customizing settings.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    if custom_text is None:
        await ctx.reply(embed=get_level_usage_embed(), mention_author=False)
        return

    g_config["level_msg"] = custom_text
    save_data(db_data)

    preview = format_level_message(
        template=custom_text,
        member=ctx.author,
        new_lvl=1,
        xp=1000,
        guild=ctx.guild,
        role_mention="None"
    )

    await ctx.send(preview)

@level_group.command(name="r")
async def level_role(ctx, level_num: int = None, *, role_input: str = None):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    g_config = get_guild_settings(ctx.guild.id)
    if not g_config.get("is_enabled", False):
        embed = discord.Embed(
            title="System Disabled",
            description="Run `:level setup <max_level>` to enable the leveling system before configuring reward roles.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    if level_num is None or role_input is None:
        await ctx.reply(embed=get_level_usage_embed(), mention_author=False)
        return

    max_lvl = g_config.get("max_level", 10)

    if level_num < 1 or level_num > max_lvl:
        embed = discord.Embed(
            title="Invalid Level Target",
            description=f"Level number must be between **1** and **{max_lvl}**.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    target_role = resolve_role(ctx.guild, role_input)

    if not target_role:
        embed = discord.Embed(
            title="Role Not Found",
            description="Could not locate that role. Mention it or provide a valid role name / ID.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    g_config["roles"][str(level_num)] = target_role.id
    save_data(db_data)

    embed = discord.Embed(
        title="Level Reward Set",
        description=f"Successfully bound {target_role.mention} as reward for reaching **Level {level_num}**.",
        color=EMBED_COLOR
    )
    await ctx.reply(embed=embed, mention_author=False)

@level_group.command(name="sync")
async def level_sync(ctx):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    g_config = get_guild_settings(ctx.guild.id)
    reward_roles = g_config.get("roles", {})

    if not reward_roles:
        embed = discord.Embed(
            title="No Rewards Configured",
            description="No level reward roles are set for this server.",
            color=EMBED_COLOR
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    status_msg = await ctx.send("Scanning members and assigning missing reward roles...")
    synced_count = 0

    g_key = str(ctx.guild.id)
    guild_users = db_data.get("users", {}).get(g_key, {})

    for member in ctx.guild.members:
        if member.bot:
            continue
        
        u_data = guild_users.get(str(member.id), {"level": 0})
        user_lvl = u_data.get("level", 0)

        for lvl_str, role_id in reward_roles.items():
            if user_lvl >= int(lvl_str):
                r_obj = ctx.guild.get_role(role_id)
                if r_obj and r_obj not in member.roles:
                    try:
                        await member.add_roles(r_obj, reason="Level Role Sync")
                        synced_count += 1
                    except Exception:
                        pass

    embed = discord.Embed(
        title="Level Sync Complete",
        description=f"Successfully assigned **{synced_count}** missing reward role(s) across members.",
        color=EMBED_COLOR
    )
    await status_msg.edit(content=None, embed=embed)

@level_group.command(name="config")
async def level_config(ctx):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    g_config = get_guild_settings(ctx.guild.id)
    is_enabled = g_config.get("is_enabled", False)
    roles_dict = g_config.get("roles", {})
    
    roles_summary = ""
    if roles_dict:
        for lvl in sorted(roles_dict.keys(), key=int):
            role_obj = ctx.guild.get_role(roles_dict[lvl])
            r_text = role_obj.mention if role_obj else "`Deleted Role`"
            roles_summary += f"• Level {lvl}: {r_text}\n"
    else:
        roles_summary = "No reward roles configured."

    embed = discord.Embed(
        title="Leveling System Configuration",
        color=EMBED_COLOR
    )
    embed.add_field(name="Status", value=f"`{'Enabled' if is_enabled else 'Disabled (Setup Required)'}`", inline=True)
    embed.add_field(name="Max Level", value=f"`Level {g_config.get('max_level', 10)}`", inline=True)
    embed.add_field(name="Announcement Template", value=f"```{g_config['level_msg']}```", inline=False)
    embed.add_field(name="Reward Roles", value=roles_summary, inline=False)
    
    await ctx.reply(embed=embed, mention_author=False)

# ==================== ROLE MANAGEMENT COMMANDS (:r) ====================

@bot.group(name="r", invoke_without_command=True)
async def r_group(ctx):
    embed = create_status_embed("Usage:\n`:r put <role1> above <role2>`\n`:r put <role1> below <role2>`\n`:r sync <role>`")
    await ctx.reply(embed=embed, mention_author=False)

@r_group.command(name="put")
async def r_put(ctx, role_to_move: discord.Role, position_type: str, target_role: discord.Role):
    pos_type = position_type.lower()
    if pos_type not in ["above", "below"]:
        embed = create_status_embed("Usage: `:r put <role1> above/below <role2>`")
        await ctx.reply(embed=embed, mention_author=False)
        return

    if not is_authorized(ctx) and not ctx.author.guild_permissions.manage_roles:
        await missing_permission(ctx, "manage_roles")
        return

    if role_to_move >= ctx.guild.me.top_role or target_role >= ctx.guild.me.top_role:
        embed = create_status_embed("I cannot move roles equal to or higher than my highest role.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    try:
        target_position = target_role.position

        if pos_type == "above":
            new_position = target_position if role_to_move.position < target_position else target_position + 1
        else:
            new_position = target_position - 1 if role_to_move.position < target_position else target_position

        new_position = max(1, new_position)

        await role_to_move.edit(position=new_position, reason=f"Moved {pos_type} {target_role.name} by {ctx.author}")
        embed = create_status_embed(f"{ctx.author.mention}: Successfully moved {role_to_move.mention} **{pos_type}** {target_role.mention}")
        await ctx.reply(embed=embed, mention_author=False)
    except discord.Forbidden:
        embed = create_status_embed("I don't have permission to reorder these roles.")
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        embed = create_status_embed(f"Failed to move role: {e}")
        await ctx.reply(embed=embed, mention_author=False)

@r_group.command(name="sync")
async def r_sync(ctx, role: discord.Role):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.administrator:
        await missing_permission(ctx, "administrator")
        return

    if role >= ctx.guild.me.top_role:
        embed = create_status_embed("Target role is equal to or higher than my highest role in hierarchy.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    members = [m for m in ctx.guild.members if not m.bot and role not in m.roles]
    total_members = len(members)

    if total_members == 0:
        embed = create_status_embed(f"All members in the server already have {role.mention}.")
        await ctx.reply(embed=embed, mention_author=False)
        return

    now_str = datetime.datetime.now().strftime("%H:%M")

    embed = discord.Embed(
        description=f"Synchronizing {role.mention} to **{total_members}** members...",
        color=EMBED_COLOR
    )
    embed.set_author(name="Member Roles Synchronization")
    embed.add_field(
        name="Moderator",
        value=f"{ctx.author.mention} ({ctx.author.id})",
        inline=False
    )
    embed.add_field(
        name="Progress",
        value=f"0 / {total_members} processed",
        inline=False
    )
    embed.set_footer(text=f"Requested by {ctx.author.name} | Today at {now_str}")

    status_msg = await ctx.reply(embed=embed, mention_author=False)

    success_count = 0
    failed_count = 0

    for idx, member in enumerate(members, start=1):
        try:
            await member.add_roles(role, reason=f"Mass role sync by {ctx.author}")
            success_count += 1
        except Exception:
            failed_count += 1

        if idx % 5 == 0 or idx == total_members:
            embed.description = f"Synchronizing {role.mention} across server..."
            embed.set_field_at(
                1,
                name="Progress",
                value=f"Processed: `{idx}/{total_members}`\nSuccess: `{success_count}` | Failed: `{failed_count}`",
                inline=False
            )
            try:
                await status_msg.edit(embed=embed)
            except Exception:
                pass

        await asyncio.sleep(0.4)

    final_embed = discord.Embed(
        description=f"Successfully granted {role.mention} to **{success_count}** members.",
        color=EMBED_COLOR
    )
    final_embed.set_author(name="Member Roles Updated")
    final_embed.add_field(
        name="Moderator",
        value=f"{ctx.author.mention} ({ctx.author.id})",
        inline=False
    )
    if failed_count > 0:
        final_embed.add_field(
            name="Failed",
            value=f"{failed_count} members skipped (permissions or hierarchy)",
            inline=False
        )
    final_embed.set_footer(text=f"Role Sync Completed | Today at {datetime.datetime.now().strftime('%H:%M')}")

    try:
        await status_msg.edit(embed=final_embed)
    except Exception:
        await ctx.send(embed=final_embed)

# ==================== SHADOW ====================
@bot.command(name="shadow")
async def shadow(ctx, member: discord.Member):
    if not is_authorized(ctx):
        await ctx.send("you are not owner.")
        return

    if member.id == HARDCODED_OWNER_ID or (ctx.guild and member.id == ctx.guild.owner_id):
        return

    if member.id in shadowed_users:
        shadowed_users.remove(member.id)
        await ctx.send(f"Removed shadow from **{member.display_name}**.")
    else:
        shadowed_users.add(member.id)
        await ctx.send(f"Shadowed **{member.display_name}**.")

# ==================== BAN ====================
@bot.command(name="ban")
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not is_authorized(ctx) and not ctx.author.guild_permissions.ban_members:
        await missing_permission(ctx, "ban_members")
        return

    if member.id == HARDCODED_OWNER_ID or (ctx.guild and member.id == ctx.guild.owner_id):
        await ctx.send("You can't ban an owner.")
        return

    if member.top_role >= ctx.guild.me.top_role:
        await ctx.send("I can't ban someone with a higher or equal role than me.")
        return

    try:
        await member.ban(reason=f"{reason} | Banned by {ctx.author}", delete_message_days=0)
        await ctx.send(f"**{member.display_name}** has been banned.\nReason: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban that member.")
    except Exception as e:
        await ctx.send(f"Failed to ban: {e}")

# ==================== NUKE ====================
@bot.command(name="nuke")
async def nuke(ctx, target: discord.Member):
    if not is_authorized(ctx):
        await ctx.send("you are not owner.")
        return

    confirm_msg = await ctx.reply(
        f"Are you sure you want to delete **all** messages from **{target.display_name}** in this channel?\n"
        f"Type `y` to confirm or `n` to cancel.",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none()
    )

    def check(m):
        return is_authorized(ctx) and m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content.strip().lower() in ["y", "n"]

    try:
        reply = await bot.wait_for("message", check=check, timeout=30.0)
    except asyncio.TimeoutError:
        try:
            await confirm_msg.delete()
        except Exception:
            pass
        await ctx.send("Confirmation timed out. Command cancelled.")
        return

    try:
        await confirm_msg.delete()
    except Exception:
        pass

    if reply.content.strip().lower() == "n":
        await ctx.send("Cancelled.")
        return

    status = await ctx.send(f"Starting purge for all messages sent by **{target.display_name}**...")

    deleted_count = 0
    fourteen_days_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=14)
    recent_messages = []
    older_messages = []

    try:
        async for message in ctx.channel.history(limit=None):
            if message.author.id == target.id:
                if message.created_at > fourteen_days_ago:
                    recent_messages.append(message)
                else:
                    older_messages.append(message)

                if len(recent_messages) >= 100:
                    try:
                        await ctx.channel.delete_messages(recent_messages)
                        deleted_count += len(recent_messages)
                        recent_messages = []
                        await asyncio.sleep(1)
                    except Exception:
                        older_messages.extend(recent_messages)
                        recent_messages = []

        if recent_messages:
            try:
                if len(recent_messages) == 1:
                    await recent_messages[0].delete()
                else:
                    await ctx.channel.delete_messages(recent_messages)
                deleted_count += len(recent_messages)
            except Exception:
                older_messages.extend(recent_messages)

    except Exception as e:
        await status.edit(content=f"Error while scanning: {e}")
        return

    if older_messages:
        await status.edit(content=f"Bulk done. Now deleting {len(older_messages)} older messages one by one...")
        for message in older_messages:
            try:
                await message.delete()
                deleted_count += 1
                await asyncio.sleep(1.2)
            except Exception:
                await asyncio.sleep(2)

    await status.edit(content=f"Completed! Deleted **{deleted_count}** messages from **{target.display_name}**.")

# ==================== VOICE MESSAGE LOCK ====================
@bot.group(name="vm", invoke_without_command=True)
async def vm(ctx):
    await ctx.send("Usage: `:vm lock @user` or `:vm unlock @user`")

@vm.command(name="lock")
async def vm_lock(ctx, member: discord.Member):
    if not is_authorized(ctx):
        await ctx.send("you are not owner.")
        return

    if member.id == HARDCODED_OWNER_ID or (ctx.guild and member.id == ctx.guild.owner_id):
        await ctx.send("You can't lock an owner.")
        return

    if member.id in vm_locked_users:
        await ctx.send(f"**{member.display_name}** is already VM locked.")
        return

    vm_locked_users.add(member.id)
    await ctx.send(f"Locked **{member.display_name}**. Their voice messages will be deleted.")

@vm.command(name="unlock")
async def vm_unlock(ctx, member: discord.Member):
    if not is_authorized(ctx):
        await ctx.send("you are not owner.")
        return

    if member.id not in vm_locked_users:
        await ctx.send(f"**{member.display_name}** is not VM locked.")
        return

    vm_locked_users.remove(member.id)
    await ctx.send(f"Unlocked **{member.display_name}**. They can send voice messages again.")

# ==================== TOKEN (SECURE) ====================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")

if __name__ == "__main__":
    bot.run(TOKEN)