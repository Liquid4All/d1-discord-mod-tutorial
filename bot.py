"""Archive solicitation reports and delete high-scoring text messages."""

import asyncio
import copy
import json
import logging
import os
from io import BytesIO
from pathlib import Path

import discord
from dotenv import load_dotenv

from classifier import (
    DELETE_THRESHOLD,
    INSTRUCTIONS,
    MODEL,
    REVIEW_THRESHOLD,
    LiquidAPIError,
    score_message,
)

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


def has_uninspected_media(message: discord.Message) -> bool:
    return bool(
        message.attachments or message.stickers or message.poll or message.components
    )


class ModerationBot(discord.Client):
    def __init__(self, channel_ids: set[int], review_channel_id: int, api_key: str):
        if not 0 <= REVIEW_THRESHOLD <= DELETE_THRESHOLD <= 1:
            raise ValueError(
                "Thresholds must satisfy 0 <= REVIEW_THRESHOLD <= DELETE_THRESHOLD <= 1."
            )
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
        source_permissions = [
            source.permissions_for(source.guild.me) for source in sources
        ]
        if not all(
            permissions.view_channel
            and permissions.read_message_history
            and permissions.manage_messages
            for permissions in source_permissions
        ) or not (
            review_permissions.view_channel
            and review_permissions.send_messages
            and review_permissions.embed_links
            and review_permissions.attach_files
        ):
            log.error(
                "Monitored channels need View Channel, Read Message History, and Manage Messages. "
                "The review channel needs View Channel, Send Messages, Embed Links, and Attach Files."
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
            # Preserve the version sent to the model while Discord events continue arriving.
            message = copy.copy(message)
            try:
                # The SDK call runs in a thread so Discord's connection stays responsive.
                probability = await asyncio.to_thread(
                    score_message, message.content, self.api_key
                )
                if probability < REVIEW_THRESHOLD:
                    log.info("Message %s: no flag (%.3f)", message.id, probability)
                    return

                await self.archive_and_moderate(message, probability)
            except LiquidAPIError as error:
                log.warning("Message %s: %s No report sent.", message.id, error)
            except discord.HTTPException as error:
                log.warning(
                    "Could not archive %s (Discord HTTP %s). No deletion attempted.",
                    message.id,
                    error.status,
                )

    async def archive_and_moderate(self, message: discord.Message, probability: float):
        should_delete = probability >= DELETE_THRESHOLD and not has_uninspected_media(
            message
        )
        status = (
            "Archived; deletion pending."
            if should_delete
            else "Review only; original left in place."
        )
        if has_uninspected_media(message):
            status = "Review only; attachments, stickers, polls, or components were not inspected."

        embed = discord.Embed(
            title="Solicitation report",
            description=discord.utils.escape_markdown(message.content[:1000]),
            color=discord.Color.orange(),
            timestamp=message.created_at,
        )
        embed.add_field(name="Model probability of yes", value=f"{probability:.4f}")
        embed.add_field(
            name="Author",
            value=f"{discord.utils.escape_markdown(str(message.author))} (ID: {message.author.id})",
            inline=False,
        )
        embed.add_field(
            name="Source",
            value=f"<#{message.channel.id}> · Message ID: {message.id}",
            inline=False,
        )
        embed.add_field(
            name="Original message", value=f"[Open while available]({message.jump_url})"
        )
        embed.set_footer(text=status)

        # Keep the full text and decision context, even when the embed excerpt is shortened.
        record = {
            "message_id": message.id,
            "server_id": message.guild.id,
            "channel_id": message.channel.id,
            "author": str(message.author),
            "author_id": message.author.id,
            "created_at": message.created_at.isoformat(),
            "edited_at": message.edited_at.isoformat() if message.edited_at else None,
            "content": message.content,
            "original_url": message.jump_url,
            "model": MODEL,
            "question": INSTRUCTIONS,
            "probability": probability,
            "review_threshold": REVIEW_THRESHOLD,
            "delete_threshold": DELETE_THRESHOLD,
            "planned_action": "delete" if should_delete else "review",
            "uninspected_media": has_uninspected_media(message),
        }
        data = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        file = discord.File(BytesIO(data), filename=f"message-{message.id}.json")
        try:
            report = await self.review_channel.send(
                embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none()
            )
        finally:
            file.close()
        log.info("Message %s: archived (%.4f). %s", message.id, probability, status)
        if not should_delete:
            return

        # Only delete after the archive upload succeeds, and recheck the scored version.
        try:
            current = await message.channel.fetch_message(message.id)
            if (
                current.content != message.content
                or current.edited_at != message.edited_at
                or has_uninspected_media(current)
            ):
                status = "Not deleted: message changed after classification. Review manually."
            else:
                await current.delete()
                status = "Deleted. Full text and decision context are saved in the attached JSON."
        except discord.NotFound:
            status = "Original message already unavailable; no deletion confirmed by this bot."
        except discord.HTTPException as error:
            status = f"Deletion not confirmed (Discord HTTP {error.status}). Check the original."

        log.info("Message %s: %s", message.id, status)
        embed.set_footer(text=status)
        try:
            await report.edit(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException as error:
            log.warning(
                "Could not update report %s (Discord HTTP %s). Outcome: %s",
                report.id,
                error.status,
                status,
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
