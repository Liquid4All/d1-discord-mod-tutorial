"""Flag possible solicitation from selected Discord text channels for human review."""

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


def parse_channel_ids(value: str) -> set[int]:
    try:
        return {int(channel_id.strip()) for channel_id in value.split(",")}
    except ValueError:
        raise ValueError(
            "DISCORD_CHANNEL_IDS must be comma-separated numeric channel IDs."
        ) from None


class ModerationBot(discord.Client):
    def __init__(self, channel_ids: set[int], review_channel_id: int, api_key: str):
        if not channel_ids or any(channel_id <= 0 for channel_id in channel_ids):
            raise ValueError("DISCORD_CHANNEL_IDS must contain positive channel IDs.")
        if review_channel_id <= 0 or review_channel_id in channel_ids:
            raise ValueError(
                "Use a positive review channel ID outside DISCORD_CHANNEL_IDS."
            )
        # Message Content must also be enabled in Discord's Developer Portal.
        intents = discord.Intents(
            guilds=True, guild_messages=True, message_content=True
        )
        super().__init__(
            intents=intents, allowed_mentions=discord.AllowedMentions.none()
        )
        self.channel_ids = set(channel_ids)
        self.review_channel_id = review_channel_id
        self.api_key = api_key
        self.review_channel = None
        # A tiny tutorial bot handles one request at a time, without building a backlog.
        self.busy = asyncio.Lock()

    async def on_ready(self):
        self.review_channel = None
        sources = [
            self.get_channel(channel_id) for channel_id in sorted(self.channel_ids)
        ]
        review = self.get_channel(self.review_channel_id)
        if not isinstance(review, discord.TextChannel) or any(
            not isinstance(source, discord.TextChannel) for source in sources
        ):
            log.error("All IDs must identify text channels the bot can access.")
            await self.close()
            return
        if any(source.guild.id != review.guild.id for source in sources):
            log.error(
                "All monitored channels and the review channel must be in the same server."
            )
            await self.close()
            return
        review_permissions = review.permissions_for(review.guild.me)
        if not all(
            source.permissions_for(source.guild.me).view_channel for source in sources
        ) or not (
            review_permissions.view_channel
            and review_permissions.send_messages
            and review_permissions.embed_links
        ):
            log.error(
                "Grant View Channel on every selected channel, plus Send Messages and Embed Links on review."
            )
            await self.close()
            return
        self.review_channel = review
        log.info(
            "Ready: watching %s; reports go to #%s",
            ", ".join(f"#{source.name}" for source in sources),
            review.name,
        )

    async def on_message(self, message: discord.Message):
        # Scope is explicit: selected channels, new human-authored text messages only.
        if (
            self.review_channel is None
            or message.guild is None
            or message.channel.id not in self.channel_ids
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
                # The SDK call runs in a thread so Discord's connection stays responsive.
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
    # Keep SDK request details out of the console.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    try:
        token = required_setting("DISCORD_BOT_TOKEN")
        api_key = required_setting("LIQUID_API_KEY")
        channel_ids = parse_channel_ids(required_setting("DISCORD_CHANNEL_IDS"))
        review_channel_id = int(required_setting("DISCORD_REVIEW_CHANNEL_ID"))
        bot = ModerationBot(channel_ids, review_channel_id, api_key)
    except ValueError as error:
        raise SystemExit(f"Check .env settings: {error}") from None
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
