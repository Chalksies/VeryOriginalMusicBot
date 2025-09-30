import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
from datetime import datetime
import asyncio
import yt_dlp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

DATA_FILE = os.getenv("DATA_FILE")

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def fetch_youtube_info(url: str) -> dict:
    ydl_opts = {"quiet": False, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", "Unknown Title"),
                "thumbnail": info.get("thumbnail", None)
            }
        except Exception:
            return {"title": "Unknown Title", "thumbnail": None}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(description="Create a new league in this channel")
@app_commands.describe(rounds="Number of rounds in this league")
async def create_league(interaction: discord.Interaction, rounds: int):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id in data:
        await interaction.response.send_message("A league already exists in this channel!", ephemeral=True)
        return
    
    if rounds < 1:
        await interaction.response.send_message("Number of rounds must be at least 1.", ephemeral=True)
        return

    data[channel_id] = {
        "players": [],
        "round": None,
        "current_round": 0,
        "max_rounds": rounds,
        "scores": {}
    }
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

    league = data[channel_id]
    if league["round"] is not None:
        await interaction.response.send_message("A round is already running. End it first.", ephemeral=True)
        return
    
    if league["current_round"] >= league["max_rounds"]:
        await interaction.response.send_message("The league has already completed all its rounds.", ephemeral=True)
        return

    league["current_round"] += 1
    league["round"] = {
        "theme": theme,
        "submissions": {},
        "votes": {},
        "phase": "submission" 
    }
    save_data(data)
    await interaction.response.send_message(
        f"**Round {league['current_round']}/{league['max_rounds']} started!**\n"
        f"**Theme:** {theme}\nUse `/submit <url>` to enter your song."
    )
@bot.tree.command(description="Submit your song for the current round")
@app_commands.describe(url="YouTube or YouTube Music link")
async def submit(interaction: discord.Interaction, url: str):
    data = load_data()
    channel_id = str(interaction.channel_id)
    player_id = str(interaction.user.id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "submission":
        await interaction.response.send_message("Submissions are only allowed during the submission phase.", ephemeral=True)
        return

    if player_id not in data[channel_id]["players"]:
        await interaction.response.send_message("You are not part of this league. Use /join_league first.", ephemeral=True)
        return

    if not ("youtube.com" in url or "youtu.be" in url or "music.youtube.com" in url):
        await interaction.response.send_message("Only YouTube or YouTube Music links are allowed.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    loop = asyncio.get_running_loop()
    yt_info = await loop.run_in_executor(None, fetch_youtube_info, url)
    title = yt_info["title"]
    thumbnail = yt_info["thumbnail"]

    data = load_data()
    data[channel_id]["round"]["submissions"][str(interaction.user.id)] = {"url": url, "title": title, "thumbnail": thumbnail}
    save_data(data)

    await interaction.edit_original_response(content=f"Submission received: [{title}]({url})")

@bot.tree.command(description="Show all submissions for the current round")
async def show_submissions(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "voting":
        await interaction.response.send_message("Submissions can only be viewed during the voting phase.", ephemeral=True)
        return

    submissions = round_data["submissions"]
    if not submissions:
        await interaction.response.send_message("No submissions yet.")
        return

    embeds = []
    for i, (player_id, sub) in enumerate(submissions.items(), start=1):
        if isinstance(sub, dict):
            url = sub.get("url", "")
            title = sub.get("title", url)
            thumbnail = sub.get("thumbnail")
        else:
            url = sub
            title = url
            thumbnail = None
        embed = discord.Embed(
            title=f"{i}. {title}",
            url=url,
            color=discord.Color.blue()
        )
        if thumbnail:
            embed.set_image(url=thumbnail)
        embeds.append(embed)

    for i in range(0, len(embeds), 10):
        batch = embeds[i:i+10]
        if i == 0:
            await interaction.response.send_message(embeds=batch)
        else:
            await interaction.followup.send(embeds=batch)

@bot.tree.command(description="Move the current round to voting phase")
async def start_voting(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "submission":
        await interaction.response.send_message("You can only start voting from the submission phase.", ephemeral=True)
        return

    if not round_data["submissions"]:
        await interaction.response.send_message("No submissions to vote on!", ephemeral=True)
        return

    round_data["phase"] = "voting"
    save_data(data)
    await interaction.response.send_message("Voting phase started! Use /show_submissions to view and /vote to vote.")

@bot.tree.command(description="Vote for a submission")
@app_commands.describe(number="The submission number you want to vote for")
async def vote(interaction: discord.Interaction, number: int):
    data = load_data()
    channel_id = str(interaction.channel_id)
    player_id = str(interaction.user.id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "voting":
        await interaction.response.send_message("Voting is only allowed during the voting phase.", ephemeral=True)
        return

    submissions = list(round_data["submissions"].items())
    if number < 1 or number > len(submissions):
        await interaction.response.send_message("Invalid submission number.", ephemeral=True)
        return

    chosen_player, _ = submissions[number - 1]
    if chosen_player == player_id:
        await interaction.response.send_message("You cannot vote for yourself.", ephemeral=True)
        return

    round_data["votes"][player_id] = chosen_player
    save_data(data)

    await interaction.response.send_message(f"You voted for submission #{number}")


@bot.tree.command(description="End the round and show results")
async def end_round(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    league = data[channel_id]
    votes = league["round"]["votes"]

    tally = {}
    for voter, voted_for in votes.items():
        tally[voted_for] = tally.get(voted_for, 0) + 1

    for player_id, count in tally.items():
        league["scores"][player_id] = league["scores"].get(player_id, 0) + count

    embed = discord.Embed(
        title=f"Results for Round {league['current_round']} ({league['round']['theme']})",
        color=discord.Color.red()
    )
    for player_id, count in tally.items():
        member = interaction.guild.get_member(int(player_id))
        name = member.display_name if member else f"User {player_id}"
        embed.add_field(name=name, value=f"{count} votes", inline=False)

    standings = sorted(league["scores"].items(), key=lambda x: x[1], reverse=True)
    standings_text = "\n".join(
        f"{interaction.guild.get_member(int(pid)).display_name if interaction.guild.get_member(int(pid)) else pid}: {pts} pts"
        for pid, pts in standings
    )
    embed.add_field(name="League Standings", value=standings_text or "No points yet", inline=False)

    league["round"] = None

    if league["current_round"] >= league["max_rounds"]:
        top_score = standings[0][1] if standings else 0
        winners = [pid for pid, pts in standings if pts == top_score]

        winner_names = ", ".join(
            interaction.guild.get_member(int(pid)).display_name if interaction.guild.get_member(int(pid)) else pid
            for pid in winners
        )

        endembed = discord.Embed(
            title=f"Results of the League!",
            color=discord.Color.gold()
    )

        endembed.add_field(
            name="League Finished!",
            value=f"Winner{'s' if len(winners) > 1 else ''}: **{winner_names}** with {top_score} points!",
            inline=False
        )

        finished = data.get("finished_leagues", {})
        channel_history = finished.get(channel_id, [])
        
        archive_entry = league.copy()
        archive_entry["finished_at"] = datetime.utcnow().isoformat()

        channel_history.append(archive_entry)
        finished[channel_id] = channel_history
        data["finished_leagues"] = finished

        del data[channel_id]

    save_data(data)
    await interaction.response.send_message(embed=embed)
    await interaction.channel.send(embed=endembed)


@bot.tree.command(description="Show current league standings")
async def standings(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data:
        await interaction.response.send_message("No league in this channel. Use /create_league first.", ephemeral=True)
        return

    league = data[channel_id]
    scores = league.get("scores", {})

    if not scores:
        await interaction.response.send_message("No points yet — play some rounds first!")
        return

    standings_sorted = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    embed = discord.Embed(
        title=f"League Standings ({league['current_round']}/{league['max_rounds']} rounds played)",
        color=discord.Color.blue()
    )

    for rank, (player_id, points) in enumerate(standings_sorted, start=1):
        member = interaction.guild.get_member(int(player_id))
        name = member.display_name if member else f"User {player_id}"
        embed.add_field(name=f"#{rank} {name}", value=f"{points} pts", inline=False)

    await interaction.response.send_message(embed=embed)

bot.run(BOT_TOKEN)