"""Flag possible solicitation from one Discord text channel for human review."""

import asyncio
import logging
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

from classifier import REVIEW_THRESHOLD, LiquidAPIError, score_message

log = logging.getLogger(__name__)


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in .env.")
    return value


class ModerationBot(discord.Client):
    def __init__(self, channel_id: int, review_channel_id: int, api_key: str):
        # Message Content must also be enabled in Discord's Developer Portal.
        intents = discord.Intents(
            guilds=True, guild_messages=True, message_content=True
        )
        super().__init__(
            intents=intents, allowed_mentions=discord.AllowedMentions.none()
        )
        if channel_id <= 0 or review_channel_id <= 0 or channel_id == review_channel_id:
            raise ValueError("Use two different, positive text channel IDs in .env.")
        self.channel_id = channel_id
        self.review_channel_id = review_channel_id
        self.api_key = api_key
        self.review_channel = None
        # A tiny tutorial bot handles one request at a time, without building a backlog.
        self.busy = asyncio.Lock()

    async def on_ready(self):
        self.review_channel = None
        source = self.get_channel(self.channel_id)
        review = self.get_channel(self.review_channel_id)
        if not isinstance(source, discord.TextChannel) or not isinstance(
            review, discord.TextChannel
        ):
            log.error("Both IDs must identify text channels the bot can access.")
            await self.close()
            return
        if source.guild.id != review.guild.id:
            log.error("Both channels must be in the same server.")
            await self.close()
            return
        source_permissions = source.permissions_for(source.guild.me)
        review_permissions = review.permissions_for(review.guild.me)
        if not source_permissions.view_channel or not (
            review_permissions.view_channel
            and review_permissions.send_messages
            and review_permissions.embed_links
        ):
            log.error(
                "Grant View Channel on both channels, plus Send Messages and Embed Links on review."
            )
            await self.close()
            return
        self.review_channel = review
        log.info("Ready: watching #%s; reports go to #%s", source.name, review.name)

    async def on_message(self, message: discord.Message):
        # Scope is explicit: one channel, new human-authored text messages only.
        if (
            self.review_channel is None
            or message.guild is None
            or message.channel.id != self.channel_id
            or message.author.bot
            or message.webhook_id is not None
            or not message.content.strip()
        ):
            return
        if self.busy.locked():
            log.warning(
                "Skipped message %s: still checking the previous message.", message.id
            )
            return

        async with self.busy:
            try:
                # HTTP runs in a thread so Discord's connection stays responsive.
                probability = await asyncio.to_thread(
                    score_message, message.content, self.api_key
                )
                if probability < REVIEW_THRESHOLD:
                    log.info("Message %s: no flag (%.3f)", message.id, probability)
                    return

                # Bound the excerpt for Discord's embed limit; prevent mention notifications.
                excerpt = discord.utils.escape_markdown(message.content[:1000])
                embed = discord.Embed(
                    title="Possible solicitation — please review",
                    description=excerpt,
                    color=discord.Color.orange(),
                )
                embed.add_field(
                    name="Model probability of yes", value=f"{probability:.3f}"
                )
                embed.add_field(
                    name="Original message", value=f"[Open message]({message.jump_url})"
                )
                embed.set_footer(
                    text="Text snapshot at posting time. No message was deleted."
                )
                await self.review_channel.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
                log.info("Message %s: sent for review (%.3f)", message.id, probability)
            except LiquidAPIError as error:
                log.warning("Message %s: %s No report sent.", message.id, error)
            except discord.HTTPException as error:
                log.warning(
                    "Could not send review for %s (Discord HTTP %s).",
                    message.id,
                    error.status,
                )


def main():
    load_dotenv(Path(__file__).with_name(".env"))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # The HTTP library's info logs are unnecessary for this tutorial.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        token = required_setting("DISCORD_BOT_TOKEN")
        api_key = required_setting("LIQUID_API_KEY")
        channel_id = int(required_setting("DISCORD_CHANNEL_ID"))
        review_channel_id = int(required_setting("DISCORD_REVIEW_CHANNEL_ID"))
        bot = ModerationBot(channel_id, review_channel_id, api_key)
    except ValueError as error:
        raise SystemExit(f"Check .env settings: {error}") from None
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
