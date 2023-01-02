import json
import discord
import discord.ext.tasks as tasks
import requests
import creds
import sqlite3
import time

class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.all()
        super().__init__(intents=intents, *args, **kwargs)
        self.tree = discord.app_commands.CommandTree(self)
        self.create_commands()
        self.sqlite_setup()

    def create_commands(self):
        @self.tree.command(
            name="link",
            description="Links a discord account's server nickname to their Minecraft username.",
        )
        async def link(
            interaction: discord.Interaction,
            discord_user: discord.User,
            minecraft_username: str,
        ):
            if not await self.check_permissions(interaction):
                return

            guild = interaction.guild
            member = guild.get_member(discord_user.id)

            # gets the player's uuid and correctly capitalized username from their username
            try:
                minecraft_username, uuid = (
                    requests.get(
                        "https://api.mojang.com/users/profiles/minecraft/"
                        + minecraft_username
                    )
                    .json()
                    .values()
                    )
            except json.JSONDecodeError:
                await interaction.response.send_message(
                    f"> The minecraft username `{minecraft_username}` does not exist.",
                    ephemeral=True,
                )
                return

            full_data = self.sqlite_cursor.execute(
                "SELECT * FROM linked_players"
            ).fetchall()

            already_used_uuid = [data for data in full_data if data[0] == guild.id and data[2] == uuid and data[1] != member.id]
            if len(already_used_uuid) > 0:
                taken_by = guild.get_member(already_used_uuid[0][1])
                if taken_by:
                    await interaction.response.send_message(
                        f"> Discord user `{taken_by}` must be unlinked from the account `{minecraft_username}` before `{member}` can be linked to it.",
                        ephemeral=True,
                    )
                    return
                else: # if they arent in the server anymore delete them from the linked_players and delete custom nick and continue
                    await self.delete_link(guild.id, taken_by)


            if (guild.id, member.id, uuid) not in full_data:

                # if it exists, deletes a player's link in a guild before creating a new one.
                self.sqlite_cursor.execute(
                    "DELETE FROM linked_players WHERE guild_id = ? AND member_id = ?",
                    (guild.id, member.id),
                )

                self.sqlite_cursor.execute(
                    "INSERT INTO linked_players VALUES (?, ?, ?)",
                    (guild.id, member.id, uuid),
                )
                self.sqlite_connection.commit()

                await self.set_nick(member, minecraft_username)

                # sends a message to tell that the account got linked
                await interaction.response.send_message(
                    f"> Discord user `{member}` **linked** with the Minecraft Account: `{minecraft_username}`",
                    ephemeral=True,
                )
                await self.update_log(guild, f"> :lock: `{member}` was linked to minecraft ign `{minecraft_username}` by `{interaction.user}`.")
            else:
                # sends a message to tell that the account was already linked
                await interaction.response.send_message(
                    f"> Discord user `{member}` was **already linked** with the Minecraft Account: `{minecraft_username}`",
                    ephemeral=True,
                )

        @self.tree.command(
            name="unlink",
            description="Unlinks a discord user from their minecraft username.",
        )
        async def unlink(interaction: discord.Interaction, discord_user: discord.User):
            if not await self.check_permissions(interaction):
                return
            guild = interaction.guild
            full_data = self.sqlite_cursor.execute(
                "SELECT * FROM linked_players"
            ).fetchall()
            if (guild.id, discord_user.id) not in [data[:2] for data in full_data]:
                await interaction.response.send_message(
                    f"> Discord user `{discord_user}` was **not** linked.",
                    ephemeral=True,
                )
            else:
                # deleting custom nicks
                
                await self.delete_link(guild.id, discord_user.id)

                # resets the nick
                await guild.get_member(discord_user.id).edit(nick=None)

                self.sqlite_connection.commit()
                await interaction.response.send_message(
                    f"> Discord user `{discord_user}` **unlinked**.",
                    ephemeral=True,
                )
                await self.update_log(guild, f"> :unlock: `{discord_user}` was unlinked by `{interaction.user}`.")

        @self.tree.command(
            name="customnick",
            description="Lets staff choose a different nick for certain usernames.",
        )
        async def customnick(
            interaction: discord.Interaction,
            discord_user: discord.User,
            custom_nickname: str = None,
        ):
            if not await self.check_permissions(interaction):
                return
            if discord_user.name == custom_nickname:
                await interaction.response.send_message(
                    f"> Custom nicknames must be different from that user's discord username.",
                    ephemeral=True,
                )
                return


            full_data = self.sqlite_cursor.execute(
                "SELECT guild_id, member_id FROM linked_players"
            ).fetchall()
            if (interaction.guild.id, discord_user.id) not in full_data:
                await interaction.response.send_message(
                    f"> User `{discord_user}` needs to be linked before setting a custom nickname.",
                    ephemeral=True,
                )
            else:
                uuid = self.sqlite_cursor.execute(
                    "SELECT mc_uuid FROM linked_players WHERE guild_id = ? AND member_id = ?",
                    (interaction.guild.id, discord_user.id),
                ).fetchone()[0]
                ign = await self.get_minecraft_username(uuid)
                # deletes other custom nicks of the same username
                self.sqlite_cursor.execute("DELETE FROM custom_nicknames WHERE guild_id = ? AND username = ?", (interaction.guild.id, ign))
                if not custom_nickname:
                    await interaction.response.send_message(
                        f"> `{discord_user}`(`{ign}`)'s custom nickname was **reset**.",
                        ephemeral=True,
                        )
                    await self.update_log(interaction.guild, f"> :arrows_counterclockwise:`{discord_user}`(`{ign}`)'s custom nickname was reset by `{interaction.user}`.")
                    await self.set_nick(interaction.guild.get_member(discord_user.id), ign)
                    self.sqlite_connection.commit()
                    return

                await self.set_nick(
                    interaction.guild.get_member(discord_user.id), custom_nickname
                )
                self.sqlite_cursor.execute(
                    "INSERT INTO custom_nicknames VALUES (?, ?, ?)",
                    (interaction.guild.id, ign, custom_nickname),
                )
                self.sqlite_connection.commit()
                await interaction.response.send_message(
                    f"> `{discord_user}`(`{ign}`) has been set the custom nickname of `{custom_nickname}`.",
                    ephemeral=True,
                )
                await self.update_log(interaction.guild, f"> :memo: `{discord_user}`(`{ign}`) was given custom nickname of `{custom_nickname}` by `{interaction.user}`.")

        @self.tree.command(
            name="channel",
            description="Choose the channel for name change logs.",
        )
        async def channel(interaction: discord.Interaction, channel: discord.TextChannel):
            if not interaction.user.guild_permissions.manage_channels:
                await interaction.response.send_message(
                    f"> `{interaction.user}` must have the `manage_channels` permission to use this command.",
                    ephemeral=True,
                )
                return
            self.sqlite_cursor.execute("DELETE FROM log_channels WHERE guild_id = ?", (interaction.guild.id,))
            self.sqlite_cursor.execute("INSERT INTO log_channels VALUES (?, ?)", (interaction.guild.id, channel.id))
            self.sqlite_connection.commit()
            await interaction.response.send_message(
                    f"> `{channel.name}` has been set as the channel for name change logs.",
                    ephemeral=True,
                )
            await channel.send("> :link: This channel is now being used for `UUID Linker` logs.")

    async def check_permissions(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_nicknames:
            await interaction.response.send_message(
                f"> `{interaction.user}` must have the `manage_nicknames` permission to use this command.",
                ephemeral=True,
            )
            return False
        return True

    def sqlite_setup(self):
        self.sqlite_connection = sqlite3.connect("players.db")
        self.sqlite_cursor = self.sqlite_connection.cursor()
        self.sqlite_cursor.execute(
            "CREATE TABLE IF NOT EXISTS linked_players(guild_id INTEGER, member_id INTEGER, mc_uuid TEXT)"
        )
        self.sqlite_cursor.execute(
            "CREATE TABLE IF NOT EXISTS custom_nicknames(guild_id INTEGER, username TEXT, custom_nick TEXT)"
        )
        self.sqlite_cursor.execute(
            "CREATE TABLE IF NOT EXISTS log_channels(guild_id INTEGER, channel_id INTEGER)"
        )
        self.sqlite_connection.commit()

    async def update_log(self, guild: discord.Guild, message: str) -> None:
        logs_data = self.sqlite_cursor.execute("SELECT * FROM log_channels")
        try:
            channel_id = [entry[1] for entry in logs_data if entry[0] == guild.id][0]
        except IndexError:
            pass
        else:
            channel = guild.get_channel(channel_id)
            if not channel:
                self.sqlite_cursor.execute("DELETE FROM log_channels WHERE guild_id = ?", (guild.id,))
                self.sqlite_connection.commit()
            else:
                await channel.send(message)

    async def get_minecraft_username(self, uuid):
        return requests.get(
                "https://sessionserver.mojang.com/session/minecraft/profile/" + uuid
            ).json()["name"]
    
    async def delete_link(self, guild_id: int, member_id: int) -> None:
        full_data = self.sqlite_cursor.execute("SELECT * FROM linked_players").fetchall()

        # deleting link
        self.sqlite_cursor.execute(
            "DELETE FROM linked_players WHERE guild_id = ? AND member_id = ?",
            (guild_id, member_id),
            )
        # deleting custom nicknames
        uuid = [data[2] for data in full_data if data[0] == guild_id and data[1] == member_id][0]
        ign = await self.get_minecraft_username(uuid)
        nickname_data = self.sqlite_cursor.execute("DELETE FROM custom_nicknames WHERE guild_id = ? AND username = ?", (guild_id, ign))

        self.sqlite_connection.commit()
        

    async def set_nick(self, member, nickname: str):
        if member.name == nickname:
            nickname = nickname[0].swapcase() + nickname[1:]
        await member.edit(nick=nickname)

    async def on_ready(self):
        await self.tree.sync()
        print(f"Logged in as {self.user}")
        if not self.update_names.is_running():
            await self.update_names.start()

    @tasks.loop(minutes=10)
    async def update_names(self):
        data = self.sqlite_cursor.execute("SELECT * FROM linked_players").fetchall()
        nickname_data = self.sqlite_cursor.execute(
                "SELECT * FROM custom_nicknames"
                ).fetchall()
        for guild_id, member_id, mc_uuid in data:
            guild = self.get_guild(
                guild_id
            )  # THIS NEEDS TO CHECK IF THE BOT IS IN THE GUILD STILL
            member = guild.get_member(member_id)

            if not member:
                await self.delete_link(guild_id, member_id)
                continue

            minecraft_username = await self.get_minecraft_username(mc_uuid)

            # checks for a custom nick
            if minecraft_username in [
                tup[1] for tup in nickname_data
            ]:  # username has a registered nickname
                correct_nickname = [
                    entry[2]
                    for entry in nickname_data
                    if entry[1] == minecraft_username
                ][0]
            else:
                if member.name == minecraft_username:
                    correct_nickname = minecraft_username[0].swapcase() + minecraft_username[1:]
                else:
                    correct_nickname = minecraft_username
            
            if not member.nick or (member.nick != correct_nickname):
                original_nickname = member.nick
                self.sqlite_cursor.execute("DELETE FROM custom_nicknames WHERE guild_id = ? AND username = ? COLLATE NOCASE", (guild.id, original_nickname))
                self.sqlite_connection.commit()
                await self.set_nick(member, correct_nickname)
                await self.update_log(guild, f"> :arrow_right_hook: `{member}`'s nick was changed from `{original_nickname}` to `{correct_nickname}`.")
            
            # sleeps for .3 secs so i dont get rate limited
            time.sleep(0.3)


def main():
    client = MyClient(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="ign changes."
        )
    )
    client.run(creds.discord_key)


if __name__ == "__main__":
    main()
