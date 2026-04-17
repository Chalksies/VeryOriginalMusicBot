import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
from datetime import datetime
from filelock import FileLock
import asyncio
import yt_dlp
import random
import io
from discord.ui import View, Button, Select
import requests
from urllib.parse import parse_qs, urlparse

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = os.getenv("DATA_FILE")
RESPONSIBLE_PERSON = int(os.getenv("RESPONSIBLE_PERSON"))
PLAYER_ROLE = int(os.getenv("PLAYER_ROLE"))
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")
SUBS_PER_PAGE = 15

def load_data():
    lock = FileLock(DATA_FILE + ".lock")
    with lock:
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r") as f:
            return json.load(f)

def save_data(data):
    lock = FileLock(DATA_FILE + ".lock")
    with lock:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)

def fetch_youtube_info(url: str) -> dict:
    ydl_opts = {"quiet": False, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            
            is_playlist = info.get("_type") == "playlist"
            playlist_warning = None
            
            if is_playlist:
                entries = info.get("entries", [])
                if entries:
                    info = entries[0]
                    playlist_warning = f"This is a playlist!  I will be submitting the first track: {info.get('title', 'Unknown')}"
            
            return {
                "title": info.get("title", "Unknown Title"),
                "thumbnail": info.get("thumbnail", None),
                "artist": info.get("uploader", info.get("channel", "Unknown Artist")),
                "explicit": info.get("age_limit", 0) >= 18,
                "duration": info.get("duration", 0),
                "is_playlist": is_playlist,
                "playlist_warning": playlist_warning,
                "video_id": info.get("id", None)
            }
        except Exception:
            return {"title": "Unknown Title", "thumbnail": None, "artist": "Unknown Artist", "explicit": False, "duration": 0, "is_playlist": False, "playlist_warning": None, "video_id": None}

def get_youtube_access_token() -> str:
    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
        print("[YouTube] Missing credentials (CLIENT_ID, CLIENT_SECRET, or REFRESH_TOKEN)")
        return None
    
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "refresh_token": YOUTUBE_REFRESH_TOKEN,
                "grant_type": "refresh_token"
            }
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            print(f"[YouTube] Token refresh failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[YouTube] Error refreshing access token: {e}")
    return None

async def create_youtube_playlist(theme: str, channel_id: str, round_num: int) -> dict:
    access_token = get_youtube_access_token()
    if not access_token:
        return {"success": False, "playlist_id": None, "url": None, "error": "No YouTube credentials"}
    
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Create playlist
        create_response = requests.post(
            "https://www.googleapis.com/youtube/v3/playlists",
            headers=headers,
            json={
                "snippet": {
                    "title": f"League Round {round_num} - {theme}",
                    "description": f"Music League submissions for the theme: {theme}"
                },
                "status": {"privacyStatus": "public"}
            },
            params={"part": "snippet,status"}
        )
        
        if create_response.status_code == 200:
            playlist_id = create_response.json().get("id")
            if not playlist_id:
                error_msg = "Playlist created but no ID in response"
                print(f"[YouTube] {error_msg}")
                return {"success": False, "playlist_id": None, "url": None, "error": error_msg}
            print(f"[YouTube] Playlist created: {playlist_id}")
            return {
                "success": True,
                "playlist_id": playlist_id,
                "url": f"https://www.youtube.com/playlist?list={playlist_id}"
            }
        else:
            error_msg = f"API returned {create_response.status_code}: {create_response.text}"
            print(f"[YouTube] Playlist creation failed: {error_msg}")
            return {"success": False, "playlist_id": None, "url": None, "error": error_msg}
    except Exception as e:
        error_msg = str(e)
        print(f"[YouTube] Error creating playlist: {error_msg}")
        return {"success": False, "playlist_id": None, "url": None, "error": error_msg}

async def add_video_to_playlist(playlist_id: str, video_id: str) -> dict:
    if not playlist_id or not video_id:
        return {"success": False, "error": "Missing playlist_id or video_id"}
    
    access_token = get_youtube_access_token()
    if not access_token:
        return {"success": False, "error": "No YouTube credentials"}
    
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.post(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            headers=headers,
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id
                    }
                }
            },
            params={"part": "snippet"}
        )
        
        if response.status_code == 200:
            return {"success": True}
        else:
            error_msg = f"API returned {response.status_code}: {response.text}"
            print(f"[YouTube] Failed to add video {video_id}: {error_msg}")
            return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = str(e)
        print(f"[YouTube] Error adding video {video_id}: {error_msg}")
        return {"success": False, "error": error_msg}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(intents=intents)

class SubmissionsView(View):
    def __init__(self, submissions, theme, requester_id=None, playlist_url=None):
        super().__init__(timeout=180)
        self.submissions = submissions
        self.theme = theme
        self.page = 0
        self.max_page = (len(submissions) - 1) // SUBS_PER_PAGE
        self.requester_id = requester_id
        self.playlist_url = playlist_url

        if self.max_page > 0: 
            options = []
            for i in range(self.max_page + 1):
                options.append(discord.SelectOption(label=f"Page {i+1}", value=str(i)))
            self.page_select.options = options
            self.update_button_states() # Set initial button state
        else:
            self.clear_items()

    def update_button_states(self):
        # We don't need to check max_page <= 0 here anymore because 
        # clear_items() handles that case by removing everything.
        for child in self.children:
            if isinstance(child, Button):
                if child.custom_id == "prev_button":
                    child.disabled = self.page == 0
                elif child.custom_id == "next_button":
                    child.disabled = self.page == self.max_page

    def build_embed(self):
        start = self.page * SUBS_PER_PAGE
        end = start + SUBS_PER_PAGE
        chunk = self.submissions[start:end]

        lines = []
        for i, sub in enumerate(chunk, start=start + 1):
            url = sub.get("url", "")
            title = sub.get("title", url)
            artist = sub.get("artist", "Unknown Artist")
            explicit = "[E] " if sub.get("explicit", False) else ""
            cw = f" | CW: {sub.get('content_warning')}" if sub.get('content_warning') else ""
            if len(title) > 80:
                title = title[:77] + "..."
            lines.append(f"{i}. {explicit}[{title}]({url}) — {artist}{cw}")

        description = "\n".join(lines) or "No submissions"
        if self.playlist_url:
            description += f"\n\n**[Listen to full playlist]({self.playlist_url})**"

        embed = discord.Embed(
            title=f"🎶 Submissions for {self.theme} (Page {self.page+1}/{self.max_page+1})",
            description=description,
            color=discord.Color.blue()
        )
        return embed

    def check_owner(self, interaction: discord.Interaction) -> bool:
        if self.requester_id and interaction.user.id != self.requester_id:
            return False
        return True

    @discord.ui.button(label="<<< Prev", style=discord.ButtonStyle.secondary, custom_id="prev_button")
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if not self.check_owner(interaction):
            await interaction.response.send_message("You can't control this pagination.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        self.update_button_states()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label=">>> Next", style=discord.ButtonStyle.secondary, custom_id="next_button")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if not self.check_owner(interaction):
            await interaction.response.send_message("You can't control this pagination.", ephemeral=True)
            return
        if self.page < self.max_page:
            self.page += 1
        self.update_button_states()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(placeholder="Jump to page...", custom_id="page_select")
    async def page_select(self, interaction: discord.Interaction, select: Select):
        if not self.check_owner(interaction):
            await interaction.response.send_message("You can't control this pagination.", ephemeral=True)
            return
        self.page = int(select.values[0])
        self.update_button_states()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")
    if not hasattr(bot, "listening_task"):
        bot.listening_task = asyncio.create_task(update_listening_status())

async def update_listening_status():
    await bot.wait_until_ready()
    while not bot.is_closed():
        data = load_data()
        all_submissions = []
        for league in data.values():
            if isinstance(league, dict) and league.get("round"):
                submissions = league["round"].get("submissions", {})
                for sub in submissions.values():
                    if isinstance(sub, dict):
                        title = sub.get("title")
                        if title and title != "Unknown Title":
                            all_submissions.append(title)
        if all_submissions:
            song = random.choice(all_submissions)
            status_text = f"listening to {song}"
        else:
            status_text = "listening to the silence..."
        await bot.change_presence(activity=discord.CustomActivity(name=status_text))
        await asyncio.sleep(300)

@bot.tree.command(description="Create a new league in this channel")
@app_commands.describe(rounds="Number of rounds in this league", votes_per_player="Number of votes each player can cast per round", max_players="Maximum number of players (0 = unlimited)")
async def create_league(interaction: discord.Interaction, rounds: int, votes_per_player: int, max_players: int = 15):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Only users with permission can create a league.", ephemeral=True)
        return

    if channel_id in data:
        await interaction.response.send_message("A league already exists in this channel!", ephemeral=True)
        return
    
    if rounds < 1:
        await interaction.response.send_message("Number of rounds must be at least 1.", ephemeral=True)
        return

    if max_players < 1 and max_players != 0:
        await interaction.response.send_message("Max players must be 0 (unlimited) or more than one. Default is 15.", ephemeral=True)
        return

    data[channel_id] = {
        "players": [],
        "round": None,
        "current_round": 0,
        "max_rounds": rounds,
        "scores": {},
        "votes_per_player": votes_per_player,
        "max_players": max_players
    }
    save_data(data)

    max_text = f" (Max {max_players} players)" if max_players > 0 else " (Unlimited players)"
    await interaction.response.send_message(f"New league created in this channel!{max_text}")

@bot.tree.command(description="Join the league in this channel")
async def join_league(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data:
        await interaction.response.send_message("No league in this channel. Use /create_league first.", ephemeral=True)
        return

    league = data[channel_id]
    max_players = league.get("max_players", 0)
    current_players = len(league["players"])

    if max_players > 0 and current_players >= max_players:
        await interaction.response.send_message(f"This league is full ({current_players}/{max_players} players).", ephemeral=True)
        return

    player_id = str(interaction.user.id)
    if player_id in league["players"]:
        await interaction.response.send_message("You are already in this league.", ephemeral=True)
        return

    league["players"].append(player_id)
    save_data(data)

    await interaction.response.send_message(f"{interaction.user.mention} joined the league! ({current_players + 1}/{max_players if max_players > 0 else '∞'})")

@bot.tree.command(description="Start a new round")
@app_commands.describe(theme="Theme for this round")
async def start_round(interaction: discord.Interaction, theme: str):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Only users with permission can start a round.", ephemeral=True)
        return

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
        "phase": "submission",
        "submissions_message_id": None,
        "submission_order": []
    }
    save_data(data)

    role = interaction.guild.get_role(PLAYER_ROLE)
    
    await interaction.response.send_message(
        f"**Round {league['current_round']}/{league['max_rounds']} started!** {role.mention}\n"
        f"**Theme:** {theme}\nUse `/submit <url>` to enter your song."
    )
@bot.tree.command(description="Submit your song for the current round")
@app_commands.describe(url="YouTube or YouTube Music link", content_warning="Content/trigger warning(s) (optional)")
async def submit(interaction: discord.Interaction, url: str, content_warning: str = None):
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
    artist = yt_info["artist"]
    explicit = yt_info["explicit"]
    playlist_warning = yt_info.get("playlist_warning")
    video_id = yt_info.get("video_id")

    data = load_data()
    data[channel_id]["round"]["submissions"][str(interaction.user.id)] = {
        "url": url,
        "title": title,
        "thumbnail": thumbnail,
        "artist": artist,
        "explicit": explicit,
        "content_warning": content_warning,
        "submitted_at": datetime.utcnow().isoformat(),
        "video_id": video_id
    }
    save_data(data)

    explicit_marker = "[E] " if explicit else ""
    cw_marker = f" | CW: {content_warning}" if content_warning else ""
    response_text = f"Submission received: [{title}]({url}) by {artist}{explicit_marker}{cw_marker}"
    
    if playlist_warning:
        response_text += f"\n\nHuh!? {playlist_warning}"
    
    await interaction.edit_original_response(content=response_text)

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

    submissions = list(round_data["submissions"].values())
    if not submissions:
        await interaction.response.send_message("No submissions yet.")
        return

    submission_order = round_data.get("submission_order", [])
    if submission_order:
        ordered_submissions = [round_data["submissions"][pid] for pid in submission_order]
    else:
        ordered_submissions = submissions
    
    playlist_url = round_data.get("playlist_url")
    
    view = SubmissionsView(ordered_submissions, round_data["theme"], requester_id=interaction.user.id, playlist_url=playlist_url)
    msg = await interaction.response.send_message(embed=view.build_embed(), view=view)
    
    if not round_data.get("submissions_message_id"):
        try:
            await msg.pin()
            round_data["submissions_message_id"] = msg.id
            save_data(data)
        except Exception:
            pass


@bot.tree.command(description="Show details for a specific submission")
@app_commands.describe(number="The submission number")
async def submission_details(interaction: discord.Interaction, number: int):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return
    
    round_data = data[channel_id]["round"]
    
    if round_data.get("phase") != "voting":
        await interaction.response.send_message("Submissions can only be viewed during the voting phase.", ephemeral=True)
        return

    # Use stored submission order for consistent display
    submission_order = round_data.get("submission_order", [])
    if submission_order:
        submissions = [round_data["submissions"][pid] for pid in submission_order]
    else:
        submissions = list(round_data["submissions"].values())

    if number < 1 or number > len(submissions):
        await interaction.response.send_message("Invalid submission number.", ephemeral=True)
        return

    sub = submissions[number - 1]
    title = sub.get("title", "Unknown Title")
    url = sub.get("url", "")
    thumbnail = sub.get("thumbnail")

    embed = discord.Embed(
        title=title,
        url=url,
        color=discord.Color.green()
    )
    if thumbnail:
        embed.set_image(url=thumbnail)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(description="Move the current round to voting phase")
async def start_voting(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Only users with permission can start voting.", ephemeral=True)
        return

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

    votes_per_player = data[channel_id]["votes_per_player"]
    round_data["phase"] = "voting"
    
    #randomize submission order once for now
    submission_ids = list(round_data["submissions"].keys())
    random.shuffle(submission_ids)
    round_data["submission_order"] = submission_ids
    
    # Try to create YouTube playlist
    league = data[channel_id]
    playlist_result = await create_youtube_playlist(
        round_data["theme"],
        channel_id,
        league["current_round"]
    )
    
    if playlist_result["success"]:
        round_data["playlist_id"] = playlist_result["playlist_id"]
        round_data["playlist_url"] = playlist_result["url"]
        
        # Add videos to playlist
        failed_videos = []
        for player_id in submission_ids:
            submission = round_data["submissions"][player_id]
            video_id = submission.get("video_id")
            if video_id:
                add_result = await add_video_to_playlist(playlist_result["playlist_id"], video_id)
                if not add_result["success"]:
                    failed_videos.append((video_id, add_result.get("error", "Unknown error")))
        
        if failed_videos:
            print(f"[YouTube] {len(failed_videos)} video(s) failed to add to playlist:")
            for vid_id, error in failed_videos:
                print(f"  - {vid_id}: {error}")
    
    save_data(data)
    
    role = interaction.guild.get_role(PLAYER_ROLE)
    
    playlist_text = ""
    if playlist_result["success"]:
        playlist_text = f"\nListen to the playlist here! ({playlist_result['url']})"
    
    await interaction.response.send_message(
        f"Voting phase started! Use /show_submissions to view and /vote to vote.\n"
        f"Total votes per player: {votes_per_player}\n\n{role.mention}{playlist_text}"
    )

@bot.tree.command(description=f"Vote for a submission (you have multiple votes per round)")
@app_commands.describe(number="The submission number you want to vote for", amount="The number of votes to allocate to this submission", comment="Optional comment about your vote")
async def vote(interaction: discord.Interaction, number: int, amount: int = 1, comment: str = None):
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

    # Use stored submission order for consistent vote mapping
    submission_order = round_data.get("submission_order", [])
    if submission_order:
        submissions = [(pid, round_data["submissions"][pid]) for pid in submission_order]
    else:
        submissions = list(round_data["submissions"].items())
    
    if number < 1 or number > len(submissions):
        await interaction.response.send_message("Invalid submission number.", ephemeral=True)
        return
    
    votes_per_player = data[channel_id]["votes_per_player"]

    if amount < 1 or amount > votes_per_player:
        await interaction.response.send_message(f"You can only allocate between 1 and {votes_per_player} votes per submission.", ephemeral=True)
        return

    chosen_player, _ = submissions[number - 1]
    if chosen_player == player_id:
        await interaction.response.send_message("You cannot vote for yourself.", ephemeral=True)
        return

    if "votes" not in round_data:
        round_data["votes"] = {}
    if player_id not in round_data["votes"]:
        round_data["votes"][player_id] = {}

    player_votes = round_data["votes"][player_id]
    current_total = sum(v["amount"] if isinstance(v, dict) else v for v in player_votes.values())
    if current_total + amount > votes_per_player:
        await interaction.response.send_message(f"You only have {votes_per_player - current_total} votes left this round.", ephemeral=True)
        return

    #always store votes as dicts for consistency
    if chosen_player not in player_votes:
        player_votes[chosen_player] = {"amount": 0}
    
    if isinstance(player_votes[chosen_player], dict):
        player_votes[chosen_player]["amount"] += amount
        if comment:
            player_votes[chosen_player]["comment"] = comment
    else:
        #convert old-style int vote to dict
        player_votes[chosen_player] = {"amount": player_votes[chosen_player] + amount}
        if comment:
            player_votes[chosen_player]["comment"] = comment
    
    save_data(data)

    remaining = votes_per_player - (current_total + amount)
    comment_text = f" | Comment: {comment}" if comment else ""
    await interaction.response.send_message(f"You gave {amount} vote(s) to submission #{number}. You have {remaining} votes left this round.{comment_text}")

@bot.tree.command(description="End the round and show results")
async def end_round(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Only users with permission can end the round.", ephemeral=True)
        return

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    league = data[channel_id]
    round_data = league["round"]
    votes = round_data["votes"]
    submissions = round_data["submissions"]
    tally = {}

    for voter, vote_dict in votes.items():
        for target, vote_data in vote_dict.items():
            amount = vote_data["amount"] if isinstance(vote_data, dict) else vote_data
            tally[target] = tally.get(target, 0) + amount

    results_sorted = sorted(tally.items(), key=lambda x: x[1], reverse=True)
    full_results_lines = ["Rank,Submitter,Song Title,Artist,Explicit,Votes,URL\n"]
    

    top_results_for_embed = []
    
    for rank, (player_id, count) in enumerate(results_sorted, start=1):
        member = interaction.guild.get_member(int(player_id))
        name = member.display_name if member else f"User {player_id}"
        
        submission = submissions.get(player_id, {})
        title = submission.get("title", "Unknown Title").replace(",", "") 
        artist = submission.get("artist", "Unknown Artist").replace(",", "")
        url = submission.get("url", "#")
        explicit = "Yes" if submission.get("explicit", False) else "No"
        
        full_results_lines.append(f"{rank},{name},{title},{artist},{explicit},{count},{url}\n")

        if rank <= 5:
            top_results_for_embed.append({"rank": rank, "name": name, "title": title, "artist": artist, "url": url, "votes": count, "explicit": submission.get("explicit", False)})

    results_content = "".join(full_results_lines)
    file_name = f"Round_{league['current_round']}_Results.csv"
    
    file_buffer = io.BytesIO(results_content.encode('utf-8'))
    discord_file = discord.File(fp=file_buffer, filename=file_name)

    del full_results_lines
    del results_content

    for player_id, count in tally.items():
        league["scores"][player_id] = league["scores"].get(player_id, 0) + count
    
    embed = discord.Embed(
        title=f"🎶 Final Tally for Round {league['current_round']} ({round_data['theme']})",
        description="The top 5 submissions are below. Find the full results attached!",
        color=discord.Color.red()
    )

    for item in top_results_for_embed:
        medals = {1: "🥇", 2: "🥈", 3: "🥉", 4: "4️⃣", 5: "5️⃣"}
        prefix = medals.get(item["rank"], f"{item['rank']}.")
        explicit_marker = "[E]" if item["explicit"] else ""

        embed.add_field(
            name=f"{prefix} #{item['rank']} - {item['name']} ({item['votes']} votes)",
            value=f"Song: **[{item['title']}]({item['url']})** by {item['artist']}{explicit_marker}",
            inline=False
        )
    
    standings = sorted(league["scores"].items(), key=lambda x: x[1], reverse=True)
    standings_text = "\n".join(
        f"{interaction.guild.get_member(int(pid)).display_name if interaction.guild.get_member(int(pid)) else pid}: {pts} pts"
        for pid, pts in standings
    )
    embed.add_field(name="\n\nCurrent League Standings", value=standings_text or "No points yet", inline=False)


    endembed = None
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
    await interaction.response.send_message(embed=embed, file=discord_file)
    if endembed:
        await interaction.channel.send(embed=endembed)

@bot.tree.command(description="Check if all players have submitted a song for the current round")
async def check_submissions(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "submission":
        await interaction.response.send_message("This command can only be used during the submission phase.", ephemeral=True)
        return

    league = data[channel_id]
    players = set(league["players"])
    submissions = set(round_data["submissions"].keys())
    missing = players - submissions
    if not missing:
        await interaction.channel.send("All players have submitted a song for this round!")
        await interaction.response.send_message("Everyone has submitted!", ephemeral=True)
    else:
        mentions = [f"<@{uid}>" for uid in missing]
        await interaction.response.send_message(f"Waiting on submissions from: {', '.join(mentions)}", ephemeral=True)

@bot.tree.command(description="Check who hasn't voted yet in the current round.")
async def check_votes(interaction: discord.Interaction):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Only users with permission can check votes.", ephemeral=True)
        return

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    if round_data.get("phase") != "voting":
        await interaction.response.send_message("This command can only be used during the voting phase.", ephemeral=True)
        return

    league = data[channel_id]
    players = set(league["players"])
    voters = set(round_data.get("votes", {}).keys())
    missing_voters = players - voters

    total = len(players)
    voted = len(voters)
    remaining = len(missing_voters)

    embed = discord.Embed(
        title="Voting Status",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Progress",
        value=f"{voted}/{total} players have voted",
        inline=False
    )

    if remaining == 0:
        embed.add_field(
            name="Status",
            value="All players have voted!!",
            inline=False
        )
    else:
        mentions = [f"<@{uid}>" for uid in missing_voters]
        embed.add_field(
            name=f"Waiting on {remaining} player{'s' if remaining != 1 else ''}",
            value=", ".join(mentions),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description="Give Melodi a hug!")
async def hug(interaction: discord.Interaction):
    await interaction.response.send_message(f"Aww, thanks for the hug {interaction.user.mention}!!! I appreciate it :3")

@bot.tree.command(description="Make her speak.")
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None, reply_to: str = None):
    
    if (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("Nuh uh.", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    target_message = None
    
    if reply_to:
        try:
            target_message = await target_channel.fetch_message(int(reply_to))
        except (ValueError, discord.NotFound):
            pass
    
    if target_message:
        await target_message.reply(content=message)
    else:
        await target_channel.send(content=message)
    
    await interaction.response.send_message("Message sent!", ephemeral=True)

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
        await interaction.response.send_message("No points yet, play some rounds first!")
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

@bot.tree.command(description="Remove a player's submission from the current round.")
async def remove_submission(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    channel_id = str(interaction.channel_id)

    if (interaction.user.guild_permissions.manage_messages == False) and (interaction.user.id != RESPONSIBLE_PERSON):
        await interaction.response.send_message("You are not authorized to do this.", ephemeral=True)
        return

    if channel_id not in data or data[channel_id]["round"] is None:
        await interaction.response.send_message("No active round in this channel.", ephemeral=True)
        return

    round_data = data[channel_id]["round"]
    player_id = str(user.id)

    if player_id not in round_data["submissions"]:
        await interaction.response.send_message(f"{user.display_name} has not submitted a song this round.", ephemeral=True)
        return

    del round_data["submissions"][player_id]
    save_data(data)
    await interaction.response.send_message(f"Submission from {user.display_name} has been removed.", ephemeral=True)

bot.run(BOT_TOKEN)