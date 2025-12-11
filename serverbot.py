import discord
from discord.ext import commands
from discord.ext import tasks
from wakeonlan import send_magic_packet
import subprocess
import socket
import asyncio
from mcrcon import MCRcon
import time
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEBIAN_MAC = os.environ.get("DEBIAN_MAC")
DEBIAN_IP = "192.168.0.32"        # IP of the Debian PC
MC_SERVER_IP = "192.168.0.32"     # IP of the Minecraft server (same machine in this case)
MC_RCON_PORT = 25575
MC_RCON_TIMEOUT = 2  # seconds
RCON_PASSWORD = os.environ.get("RCON_PASSWORD")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

def get_minecraft_players(host, port, password):
    """Returns (count, [player names])"""
    try:
        with MCRcon(host, password, port=port) as mcr:
            resp = mcr.command("list")  # returns string like "There are 1/20 players online: Steve"
            # Parse count and player names
            if "There are" in resp:
                parts = resp.split(":")
                if len(parts) == 2:
                    player_str = parts[1].strip()
                    players = [p.strip() for p in player_str.split(",")] if player_str else []
                    count = len(players)
                    return count, players
                else:
                    return 0, []
            else:
                return 0, []
    except Exception as e:
        print("RCON error:", e)
        return 0, []


def is_online(ip):
    """Check if the Debian PC is online via ping."""
    try:
        output = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return output.returncode == 0
    except:
        return False

def is_minecraft_online(host, port, timeout=2):
    """Check if the Minecraft server is responding on the RCON port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False

@bot.command()
async def hello(ctx):
    await ctx.send("hello")

@bot.command()
async def commands(ctx):
    """Show all available commands and what they do."""
    help_text = """
**Available Commands**

**!wake** — Sends a Wake-on-LAN packet to the Debian server. Waits for the PC and Minecraft server to be online and notifies you.

**!status** — Checks whether the Debian server and the Minecraft server are online.

**!players** — Shows how many players are online on the Minecraft server and lists their usernames.

**!hello** — Simple test command that responds with 'hello'.
"""
    await ctx.send(help_text)

@bot.command()
async def players(ctx):
    """Check how many players are online and who they are."""
    pc_online = is_online(DEBIAN_IP)
    if not pc_online:
        await ctx.send("🔴 The server PC is offline!")
        return

    mc_online = is_minecraft_online(MC_SERVER_IP, MC_RCON_PORT)
    if not mc_online:
        await ctx.send("⚠️ Minecraft server is not running.")
        return

    count, player_list = get_minecraft_players(MC_SERVER_IP, MC_RCON_PORT, RCON_PASSWORD)
    if count == 0:
        await ctx.send("🟢 Minecraft server is online, but no players are currently connected.")
    else:
        players_str = ", ".join(player_list)
        await ctx.send(f"🟢 Minecraft server online — {count} player(s) connected: {players_str}")

@bot.command()
async def wake(ctx):
    """Wake the Debian server and notify when PC + Minecraft are online."""
    start_time = time.time()
    if is_online(DEBIAN_IP):
        await ctx.send("🟢 The server PC is already online!")

        if is_minecraft_online(MC_SERVER_IP, MC_RCON_PORT):
            await ctx.send("🎮 Minecraft server is already running as well!")
        else:
            await ctx.send("⏳ PC is online but Minecraft server isn't responding yet.")
        return

    send_magic_packet(DEBIAN_MAC)
    await ctx.send("🔌 Sent WOL packet! Waiting for server to start…")

    # Poll for PC -> up
    for i in range(60):  # ~60 seconds max
        if is_online(DEBIAN_IP):
            await ctx.send("🟢 The server PC is now online! Waiting for Minecraft…")
            break
        await asyncio.sleep(1)
    else:
        await ctx.send("❌ The server PC didn’t come online in time.")
        return

    # Poll for Minecraft server -> up
    for i in range(90):  # ~90 seconds max
        if is_minecraft_online(MC_SERVER_IP, MC_RCON_PORT):
            end_time = time.time()  # <-- mark when server is fully ready
            elapsed = end_time - start_time
            await ctx.send(f"🎉 Minecraft server is live! Startup time: {elapsed:.1f} seconds")
            return
        await asyncio.sleep(2)

    await ctx.send("⚠️ PC is online, but Minecraft server didn't start.")

@bot.command()
async def status(ctx):
    """Check Debian PC and Minecraft server status."""
    pc_online = is_online(DEBIAN_IP)

    if pc_online:
        mc_online = is_minecraft_online(MC_SERVER_IP, MC_RCON_PORT)
    else:
        mc_online = False

    pc_status = "🟢 Online" if pc_online else "🔴 Offline"
    mc_status = "🟢 Running" if mc_online else "🔴 Not running"

    await ctx.send(f"**PC Status:** {pc_status}\n**Minecraft Server:** {mc_status}")

CHECK_INTERVAL=300
INACTIVITY_THRESHOLD=30*60

async def auto_shutdown_task():
    idle_time = 0
    await bot.wait_until_ready()  # wait for bot to be ready
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        if is_online(DEBIAN_IP) and is_minecraft_online(MC_SERVER_IP, MC_RCON_PORT):
            count, _ = get_minecraft_players(MC_SERVER_IP, MC_RCON_PORT, RCON_PASSWORD)
            if count == 0:
                idle_time += CHECK_INTERVAL
            else:
                idle_time = 0
        else:
            idle_time = 0


        if idle_time >= INACTIVITY_THRESHOLD:
            if channel:
                await channel.send("⚠️ Minecraft server has been idle. Shutting down...")
            subprocess.run(["ssh", f"harrykerans@{DEBIAN_IP}", "sudo shutdown now"])
            idle_time = 0

        await asyncio.sleep(CHECK_INTERVAL)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(auto_shutdown_task())

# Run the bot normally
bot.run(BOT_TOKEN)
