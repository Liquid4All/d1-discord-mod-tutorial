# A Discord moderation bot with `d1-ultra`

Build a bot that asks one question: **is this message unsolicited solicitation?**

It reads new messages in one text channel and sends likely sales, recruitment,
or signup pitches to a private moderator channel for review. It never deletes
messages or bans anyone.

```text
Message → d1-ultra → probability ≥ 0.80? → send a review report
```

This is a small piece of the original bot's spam/advertising rule. Project demos,
technical questions, and scam warnings should be allowed; a link alone isn't a
violation. We assume unsolicited promotion is not allowed in the selected channel.

**Just want to try d1-ultra?** Open the [notebook](notebooks/d1_ultra_solicitation.ipynb).
It walks through the API independently of Discord.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Liquid4All/d1-discord-mod-tutorial/blob/main/notebooks/d1_ultra_solicitation.ipynb)

## 1. Install

You need Python 3.11+ (3.12 recommended), a Liquid API key with available credit,
and a Discord test server where you have **Manage Server** permission.

```sh
git clone https://github.com/Liquid4All/d1-discord-mod-tutorial.git
cd d1-discord-mod-tutorial
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use `py -3 -m venv .venv` and `.venv\Scripts\Activate.ps1`
for the environment commands.

Copy `.env.example` to a new file named `.env`. Keep your keys in `.env`, which
Git ignores. Leave the example file empty.

## 2. Get your API key and bot token

**Liquid:** open [Liquid Console](https://console.liquid.ai), select your
organization, then go to **Dashboard → API Keys** and create a key.

**Discord:** open the [Developer Portal](https://discord.com/developers/applications):

1. Create a **New Application** named `d1-mod-tutorial`.
2. Open **Bot → Reset Token** and copy the token.
3. Enable **Message Content Intent** on the Bot page and save.
4. Under **Installation**, enable **Guild Install**, select **Discord Provided
   Link**, and add the **bot** scope to the Guild Install defaults.
5. Select **View Channels**, **Send Messages**, and **Embed Links** permissions.
6. Open the install link, choose **Add to server**, and select your test server.

The bot needs neither Administrator nor Manage Messages permission. It will
appear offline until you run it.

## 3. Choose two channels

Create `#bot-test` and a **private** `#mod-review` text channel in the same server.
Give the bot **View Channel** in both, plus **Send Messages** and **Embed Links**
in `#mod-review`. Keep the review channel visible only to moderators and the bot.

In Discord, enable **User Settings → Advanced → Developer Mode**. Right-click
each channel and choose **Copy Channel ID**. Fill in your local `.env`:

```dotenv
LIQUID_API_KEY=your_liquid_key_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=your_bot_test_channel_id
DISCORD_REVIEW_CHANNEL_ID=your_private_mod_review_channel_id
```

Replace the placeholders with your keys and the two numeric channel IDs.

## 4. Understand the decision

[classifier.py](classifier.py) defines the question and sends this request:

```python
{
    "model": "d1-ultra",
    "state": message,
    "questions": {
        "solicitation": {"type": "noul", "instructions": INSTRUCTIONS}
    },
}
```

`state` is the message text. `INSTRUCTIONS` explains what counts as solicitation.
`noul` is the yes/no question type. The response contains a probability:

```python
probability = result["answers"]["solicitation"]["noul"]
```

[bot.py](bot.py) sends a private report when that probability is at least `0.80`.
This threshold is a starting point, not an accuracy guarantee. Adjust
`REVIEW_THRESHOLD` in `classifier.py` to flag more or fewer messages.

## 5. Run it

With your virtual environment active:

```sh
python bot.py
```

Wait for `Ready: watching #bot-test; reports go to #mod-review`. Then post these
invented examples in `#bot-test` from your own account, one at a time:

| Message | Desired result |
|---|---|
| Buy my AI consulting package. DM me for pricing. | Review report |
| Join my business opportunity. DM to earn money. | Review report |
| I built an open-source chatbot. Feedback is welcome. | No flag |
| How do I run this model locally? | No flag |
| Watch out for “DM me to earn money” messages — it's a scam. | No flag |

Wait for each result in the terminal before posting the next message. A report
contains an excerpt, the model's probability, and a link to the original. These
are desired results, not measured predictions; the model can make mistakes.

Keep the program running while testing. Press **Ctrl+C** to stop. Restart after
changing settings or code.

## Try the API notebook

Run [the notebook](notebooks/d1_ultra_solicitation.ipynb) in Colab, VS Code, or Jupyter.
It needs only a Liquid API key, entered through a hidden prompt. It does not load
`.env` automatically. A full run makes seven paid requests; the final threshold
experiment reuses the results without more calls. No GPU is needed.

## Scope and troubleshooting

This example checks only new text in the selected channel. It skips bots,
webhooks, DMs, threads, edits, and old messages. It does not inspect images,
attachments, profiles, or linked pages. Each check sends the message text and
question to Liquid in one paid API request.

The bot handles one request at a time and logs a warning if another message
arrives while it is busy. Failed requests are logged without retrying. Use a
quiet test channel for this tutorial.

- **Online but silent:** check Message Content Intent, the channel ID, and send
  a new text message from a human account.
- **Can't access a channel or send reports:** check channel permissions,
  especially in the private review channel.
- **Liquid HTTP 401 / 402 / 403 / 429:** check your API key / credit / model
  access / rate limit, respectively.

References: [Liquid API](https://docs.liquid.ai/lfm/models/d1),
[Discord installation](https://docs.discord.com/developers/quick-start/getting-started),
[message intents](https://discordpy.readthedocs.io/en/stable/intents.html#message-content).
