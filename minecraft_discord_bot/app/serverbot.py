
import asyncio
import json
import os
import re
import subprocess
import time
import traceback
import socket
import struct

import discord
from discord.ext import commands, tasks


# --------------------------------------------------
# Configuration
# --------------------------------------------------

with open("/data/options.json") as f:
    options = json.load(f)

BOT_TOKEN = options.get("BOT_TOKEN")
RCON_PASSWORD = options.get("RCON_PASSWORD")
CHANNEL_ID = options.get("CHANNEL_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from add-on options")

if not RCON_PASSWORD:
    raise RuntimeError("RCON_PASSWORD is missing from add-on options")

DOCKER_IP = "192.168.1.148"

MC_SERVER_IP = DOCKER_IP
MC_RCON_PORT = 25575

SSH_USER = "minecraftbot"
SSH_KEY = "/ssl/mc_bot_docker_key"
SSH_KNOWN_HOSTS = "/ssl/mc_bot_known_hosts"
CONTROL_SCRIPT = "/usr/local/sbin/minecraft-control"

CHECK_INTERVAL = 300
INACTIVITY_THRESHOLD = 30 * 60

idle_time = 0


# --------------------------------------------------
# SSH control
# --------------------------------------------------


def run_control(action):
    """Run an authorised Minecraft control command over SSH."""

    if action not in ("start", "stop", "status"):
        raise ValueError("Invalid Minecraft control action")

    command = [
        "ssh",
        "-i", SSH_KEY,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS}",
        f"{SSH_USER}@{DOCKER_IP}",
        f"sudo -n {CONTROL_SCRIPT} {action}",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    output = result.stdout.strip()

    if action == "status":
        # docker compose ps outputs a table.
        # An empty table means the container isn't running.
        return "running" if "minecraft" in output and "Up " in output else "stopped"

    return output



# --------------------------------------------------
# Minecraft RCON
# --------------------------------------------------

def get_minecraft_players():
    """Connect to Minecraft using the RCON protocol and retrieve players."""

    def send_packet(sock, request_id, packet_type, payload):
        body = (
            struct.pack("<ii", request_id, packet_type)
            + payload.encode("utf-8")
            + b"\x00\x00"
        )

        sock.sendall(struct.pack("<i", len(body)) + body)

    def receive_packet(sock):
        length_data = sock.recv(4)
        if len(length_data) != 4:
            raise ConnectionError("Incomplete RCON packet length")

        length = struct.unpack("<i", length_data)[0]
        data = b""

        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError("RCON connection closed")
            data += chunk

        request_id, packet_type = struct.unpack("<ii", data[:8])
        payload = data[8:-2].decode("utf-8", errors="replace")

        return request_id, packet_type, payload

    try:
        with socket.create_connection(
            (MC_SERVER_IP, MC_RCON_PORT), timeout=5
        ) as sock:

            sock.settimeout(5)

            # Authenticate
            send_packet(sock, 1, 3, RCON_PASSWORD)
            request_id, packet_type, _ = receive_packet(sock)

            if request_id == -1:
                raise PermissionError("RCON authentication failed")

            if request_id != 1 or packet_type != 2:
                raise ConnectionError("Unexpected RCON authentication response")

            # Request player list
            send_packet(sock, 2, 2, "list")
            request_id, packet_type, response = receive_packet(sock)

            if request_id != 2:
                raise ConnectionError("Unexpected RCON response ID")

            match = re.search(r"There are\s+(\d+)", response)

            if not match:
                raise ValueError(f"Could not parse player count: {response}")

            count = int(match.group(1))

            players = []
            if ":" in response:
                players_text = response.split(":", 1)[1].strip()
                if players_text:
                    players = [p.strip() for p in players_text.split(",")]

            return count, players

    except Exception as e:
        print(
            f"RCON error: {type(e).__name__}: {e}",
            flush=True
        )
        return None


# --------------------------------------------------
# Discord setup
# --------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def send_notification(message):
    """Send an automatic notification to the configured channel."""

    if not CHANNEL_ID:
        print("CHANNEL_ID not configured; notification:", message)
        return

    try:
        channel = bot.get_channel(int(CHANNEL_ID))

        if channel is None:
            channel = await bot.fetch_channel(int(CHANNEL_ID))

        await channel.send(message)

    except Exception as e:
        print("Could not send Discord notification:", e)


# --------------------------------------------------
# Commands
# --------------------------------------------------

@bot.command()
async def hello(ctx):
    await ctx.send("hello")


@bot.command(name="commands")
async def show_commands(ctx):
    help_text = """
**Available Commands**

**!wake** — Starts Minecraft on the Docker VM.

**!status** — Checks the Docker VM and Minecraft server.

**!players** — Lists connected Minecraft players.

**!hello** — Simple test command.
"""
    await ctx.send(help_text)


@bot.command()
async def wake(ctx):
    """Start Minecraft if it isn't already running."""

    start_time = time.monotonic()

    await ctx.send("🔎 Checking Minecraft server...")

    try:
        status = await asyncio.to_thread(run_control, "status")

        if status == "running":
            result = await asyncio.to_thread(get_minecraft_players)

            if result is not None:
                await ctx.send("🎮 Minecraft is already running!")
                return

            await ctx.send(
                "⏳ Minecraft is running, but RCON isn't responding yet. "
                "Waiting for it to become available..."
            )

        else:
            await ctx.send("🔌 Starting Minecraft...")
            await asyncio.to_thread(run_control, "start")

    except Exception as e:
        print("Minecraft start error:", e)
        await ctx.send("❌ Could not start Minecraft. Check the bot logs.")
        return

    # Wait up to 120 seconds for RCON to respond.
    for _ in range(60):
        await asyncio.sleep(2)

        result = await asyncio.to_thread(get_minecraft_players)

        if result is not None:
            elapsed = time.monotonic() - start_time
            await ctx.send(
                f"🎉 Minecraft is ready! "
                f"Startup time: {elapsed:.1f} seconds."
            )
            return

    await ctx.send(
        "⚠️ Minecraft was started, but RCON didn't respond "
        "within 120 seconds."
    )


@bot.command()
async def status(ctx):
    """Check the Docker VM and Minecraft server."""

    try:
        server_status = await asyncio.to_thread(run_control, "status")
    except Exception as e:
        print("Status SSH error:", e)
        await ctx.send("🔴 Cannot contact the Docker VM over SSH.")
        return

    if server_status != "running":
        await ctx.send(
            "🟢 Docker VM: Online\n"
            "🔴 Minecraft: Stopped"
        )
        return

    result = await asyncio.to_thread(get_minecraft_players)

    if result is None:
        await ctx.send(
            "🟢 Docker VM: Online\n"
            "🟠 Minecraft container: Running\n"
            "🔴 RCON: Not responding"
        )
        return

    count, players = result

    await ctx.send(
        "🟢 Docker VM: Online\n"
        "🟢 Minecraft: Running\n"
        f"👥 Players: {count}"
    )


@bot.command()
async def players(ctx):
    """List connected Minecraft players."""

    try:
        server_status = await asyncio.to_thread(run_control, "status")
    except Exception as e:
        print("Status SSH error:", e)
        await ctx.send("🔴 Cannot contact the Docker VM.")
        return

    if server_status != "running":
        await ctx.send("🔴 Minecraft server is stopped.")
        return

    result = await asyncio.to_thread(get_minecraft_players)

    if result is None:
        await ctx.send("⚠️ Minecraft is running, but RCON isn't responding.")
        return

    count, player_list = result

    if count == 0:
        await ctx.send(
            "🟢 Minecraft is online, but nobody is currently connected."
        )
    else:
        players_str = ", ".join(player_list)
        await ctx.send(
            f"🟢 Minecraft online — {count} player(s) connected: "
            f"{players_str}"
        )


# --------------------------------------------------
# Automatic inactivity shutdown
# --------------------------------------------------

@tasks.loop(seconds=CHECK_INTERVAL)
async def auto_shutdown_task():
    global idle_time

    try:
        server_status = await asyncio.to_thread(run_control, "status")

        if server_status != "running":
            idle_time = 0
            return

        result = await asyncio.to_thread(get_minecraft_players)

        # Never shut down if we cannot reliably check player count.
        if result is None:
            print("RCON unavailable; inactivity timer reset.")
            idle_time = 0
            return

        count, players = result

        if count > 0:
            idle_time = 0
            print(f"{count} player(s) online; inactivity timer reset.")
            return

        idle_time += CHECK_INTERVAL

        print(
            f"No players online. "
            f"Inactivity: {idle_time}/{INACTIVITY_THRESHOLD} seconds."
        )

        if idle_time >= INACTIVITY_THRESHOLD:
            await send_notification(
                "⚠️ Minecraft has been empty for 30 minutes. "
                "Stopping the Minecraft server..."
            )

            await asyncio.to_thread(run_control, "stop")

            idle_time = 0

            await send_notification(
                "🛑 Minecraft has been stopped. "
                "Use !wake to start it again."
            )

    except Exception as e:
        print("Automatic shutdown error:", e)
        idle_time = 0


@auto_shutdown_task.before_loop
async def before_auto_shutdown():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if not auto_shutdown_task.is_running():
        auto_shutdown_task.start()


# --------------------------------------------------
# Start bot
# --------------------------------------------------

bot.run(BOT_TOKEN)
