import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


DATA_FILE = "leagues.json"  

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(description="Create a new league in this channel")
async def create_league(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id in data:
        await interaction.response.send_message("A league already exists in this channel!", ephemeral=True)
        return

    data[channel_id] = {"players": [], "round": None}
    save_data(data)

    await interaction.response.send_message("New league created in this channel!")

@bot.tree.command(description="Join the league in this channel")
async def join_league(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data:
        await interaction.response.send_message("No league in this channel. Use /create_league first.", ephemeral=True)
        return

    player_id = str(interaction.user.id)
    if player_id in data[channel_id]["players"]:
        await interaction.response.send_message("You are already in this league.", ephemeral=True)
        return

    data[channel_id]["players"].append(player_id)
    save_data(data)

    await interaction.response.send_message(f"{interaction.user.mention} joined the league!")

@bot.tree.command(description="Start a new round")
@app_commands.describe(theme="Theme for this round")
async def start_round(interaction: discord.Interaction, theme: str):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data:
        await interaction.response.send_message("No league in this channel. Use /create_league first.", ephemeral=True)
        return

    if data[channel_id]["round"] is not None:
        await interaction.response.send_message("A round is already running. End it first.", ephemeral=True)
        return

    data[channel_id]["round"] = {
        "theme": theme,
        "submissions": {},
        "votes": {}
    }
    save_data(data)

    await interaction.response.send_message(f"Round started!\n**Theme:** {theme}\nUse `/submit <url>` to enter your song.")

@bot.tree.command(description="Submit your song for the current round")
@app_commands.describe(url="YouTube or YouTube Music link")
async def submit(interaction: discord.Interaction, url: str):
    data = load_data()
    channel_id = str(interaction.channel_id)
    player_id = str(interaction.user.id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    if player_id not in data[channel_id]["players"]:
        await interaction.response.send_message("You are not part of this league. Use /join_league first.", ephemeral=True)
        return

    if not ("youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url):
        await interaction.response.send_message("Only YouTube or YouTube Music links are allowed.", ephemeral=True)
        return

    data[channel_id]["round"]["submissions"][player_id] = url
    save_data(data)

    await interaction.response.send_message(f"✅ Submission received: {url}")

@bot.tree.command(description="Show all submissions for the current round")
async def show_submissions(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    submissions = data[channel_id]["round"]["submissions"]
    if not submissions:
        await interaction.response.send_message("No submissions yet.")
        return

    embed = discord.Embed(
        title=f"🎶 Submissions for {data[channel_id]['round']['theme']}",
        color=discord.Color.blue()
    )
    for i, (player_id, url) in enumerate(submissions.items(), start=1):
        embed.add_field(name=f"{i}.", value=url, inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(description="Vote for a submission")
@app_commands.describe(number="The submission number you want to vote for")
async def vote(interaction: discord.Interaction, number: int):
    data = load_data()
    channel_id = str(interaction.channel_id)
    player_id = str(interaction.user.id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    submissions = list(data[channel_id]["round"]["submissions"].items())
    if number < 1 or number > len(submissions):
        await interaction.response.send_message("Invalid submission number.", ephemeral=True)
        return

    chosen_player, _ = submissions[number - 1]
    if chosen_player == player_id:
        await interaction.response.send_message("You cannot vote for yourself.", ephemeral=True)
        return

    data[channel_id]["round"]["votes"][player_id] = chosen_player
    save_data(data)

    await interaction.response.send_message(f"You voted for submission #{number}")

@bot.tree.command(description="Show results of the current round and end it")
async def results(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    votes = data[channel_id]["round"]["votes"]
    tally = {}
    for voter, voted_for in votes.items():
        tally[voted_for] = tally.get(voted_for, 0) + 1

    embed = discord.Embed(
        title=f"🏆 Results for {data[channel_id]['round']['theme']}",
        color=discord.Color.gold()
    )
    for player_id, count in tally.items():
        member = interaction.guild.get_member(int(player_id))
        name = member.display_name if member else f"User {player_id}"
        embed.add_field(name=name, value=f"{count} votes", inline=False)

    data[channel_id]["round"] = None
    save_data(data)

    await interaction.response.send_message(embed=embed)

bot.run(BOT_TOKEN)