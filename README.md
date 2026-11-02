# Build a simple Discord moderation bot with d1-ultra

Teach a bot one decision: **is this message unsolicited solicitation?**

The bot watches one text channel, sends each new message's text to Liquid AI's
`d1-ultra`, and flags likely solicitation in a private moderator channel. A person
reviews the flag. The bot never deletes messages, bans members, or sends DMs.

```text
New message → d1-ultra → probability of solicitation
                           ├─ ≥ 0.80 → send a private review report
                           └─ < 0.80 → no flag
```

**Want to learn just the API?** Start with the self-contained
[d1-ultra notebook](notebooks/d1_ultra_solicitation.ipynb). It needs only a Liquid
API key; you can run it without Discord or a GPU.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Liquid4All/d1-discord-mod-tutorial/blob/main/notebooks/d1_ultra_solicitation.ipynb)

## Why this decision?

The original bot checked six server rules, plus profiles, images, and recent
cross-posts. This tutorial takes one small part of its spam/advertising rule:
solicitation. That can be illustrated with a single message and a single question;
channel relevance and cross-posting would need extra context.

Our example policy assumes unsolicited commercial pitches, recruiting, and
external signup/server invitations are not allowed in the monitored channel.
Useful project sharing is welcome.

| Message | Desired decision |
|---|---|
| “Buy my AI consulting package. DM me for pricing.” | Flag for review |
| “Join my business opportunity. No experience needed. DM to earn money.” | Flag for review |
| “Verify your account by joining this external support server.” | Flag for review |
| “I built an open-source chatbot. Feedback on the code is welcome.” | No flag |
| “How do I run this model locally?” | No flag |
| “Watch out for messages saying ‘DM me to earn money’ — it's a scam.” | No flag |

These are synthetic teaching examples, not measured model results. A link alone
is not a violation. “No flag” only means this one detector did not raise a report.

## 1. Get the code and install three libraries

You need **Python 3.11 or newer** (3.12 recommended), a Liquid account with API
access/credit, and a Discord test server where you have **Manage Server** permission.

```sh
git clone https://github.com/Liquid4All/d1-discord-mod-tutorial.git
cd d1-discord-mod-tutorial
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use `py -3 -m venv .venv` and `.venv\Scripts\Activate.ps1`
instead of the two environment commands above. All remaining commands use `python`.

Make a local copy of `.env.example` named `.env`. This file holds your keys and
channel IDs. Git ignores it; keep `.env.example` empty for other readers.

## 2. Get a Liquid API key

Sign in to [Liquid Console](https://console.liquid.ai), select your organization,
then open **Dashboard → API Keys** and create a key. Paste it into your local `.env`:

```dotenv
LIQUID_API_KEY=your_liquid_key_here
```

The model runs through the hosted API. Each inspected message makes one paid
request. See [Liquid's API documentation](https://docs.liquid.ai/lfm/models/d1)
for access and current pricing information.

## 3. Create and install a Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create a **New Application** named `d1-mod-tutorial`.
2. Open **Bot → Reset Token** and copy the token into `DISCORD_BOT_TOKEN` in `.env`.
3. On the **Bot** page, enable **Message Content Intent** and save. The code
   requests this intent too, so the bot can read message text.
4. Open **Installation**, enable **Guild Install**, and select **Discord Provided
   Link**. Under the Guild Install defaults, add the **bot** scope and these
   permissions: **View Channels**, **Send Messages**, and **Embed Links**.
5. Open the install link, select **Add to server**, choose your test server,
   and authorize the bot.

This bot uses message events; there are no slash commands to register. It needs
neither Administrator nor Manage Messages permission. It appears offline until
you run the program. See Discord's [installation guide](https://docs.discord.com/developers/quick-start/getting-started)
and the [message intent guide](https://discordpy.readthedocs.io/en/stable/intents.html#message-content)
if the portal settings differ.

## 4. Choose two text channels

Create `#bot-test` and a **private** `#mod-review` channel in the same test server.
Give the bot **View Channel** in both, plus **Send Messages** and **Embed Links**
in `#mod-review`. Keep that review channel visible only to your moderators and
the bot. Private channel permissions can override the installation permissions.

In Discord, enable **User Settings → Advanced → Developer Mode**. Right-click
each channel and choose **Copy Channel ID**. Finish your local `.env`:

```dotenv
LIQUID_API_KEY=your_liquid_key_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=your_bot_test_channel_id
DISCORD_REVIEW_CHANNEL_ID=your_private_mod_review_channel_id
```

Use the actual numeric channel IDs, not names, server IDs, or application IDs.

## 5. Understand the decision

Read [classifier.py](classifier.py). Its request has three parts:

```python
{
    "model": "d1-ultra",
    "state": message,
    "questions": {
        "solicitation": {"type": "noul", "instructions": INSTRUCTIONS}
    },
}
```

`state` is the message text. `INSTRUCTIONS` defines our policy, including the
distinction between a sales pitch and a project showcase. `noul` is the API's
yes/no question type. Read its probability from:

```python
probability = result["answers"]["solicitation"]["noul"]
```

The action is ordinary Python in [bot.py](bot.py): below `REVIEW_THRESHOLD`,
return; otherwise, send a review report. The default `0.80` is a starting point,
not a claim of 80% accuracy. Raising it generally creates fewer flags; lowering
it generally creates more. Try the notebook examples before changing it.

## 6. Run and try it

With the environment from step 1 active:

```sh
python bot.py
```

Wait for `Ready: watching #bot-test; reports go to #mod-review`. Then post the
synthetic examples above **one at a time**, waiting for the result in the terminal
before posting the next. Post from your own Discord account; other bots and
webhooks are skipped.

For a flagged message, `#mod-review` gets a text excerpt, the model's probability,
and a link to the original. Lower scores produce a `no flag` terminal log. The
original message stays in place in either case. Compare the behavior with the
desired decisions; model predictions can differ.

Leave the process running while testing. Press **Ctrl+C** to stop, and restart
after changing `.env`, the instructions, or the threshold.

## 7. Explore d1-ultra in the notebook

Open [the notebook](notebooks/d1_ultra_solicitation.ipynb) in Colab using the badge
above, or locally in VS Code/Jupyter. For a local Jupyter session:

```sh
python -m pip install jupyterlab
python -m jupyter lab notebooks/d1_ultra_solicitation.ipynb
```

Run cells from top to bottom. You'll define the question, inspect the request,
call d1-ultra, read the probability, and compare six examples. The key is entered
through a hidden prompt or read from `LIQUID_API_KEY` in your environment. The
notebook does not load `.env` automatically. A full run makes **seven paid API
requests**; the threshold experiment reuses the results without more calls.

## Keep the tutorial small

- Only new, nonempty text messages in the exact selected channel are checked.
  DMs, threads, edits, old messages, and messages posted while offline are not checked.
- Images, attachments, profiles, conversation history, and linked web pages are
  not inspected. Only message text and the policy question go to Liquid. A flagged
  text excerpt is copied into the private review channel.
- One request is handled at a time. Messages arriving while the bot is busy are
  skipped with a terminal warning. This keeps the example small; it is intended
  for a quiet test channel, not complete production moderation coverage.
- Failed requests or failed report sends are logged and not retried. Messages
  remain untouched. There is no database, deployment setup, or automatic enforcement.

## Troubleshooting

| Symptom | Check |
|---|---|
| Missing setting or invalid ID | Fill all four `.env` values; IDs must be numbers. |
| Bot is online but silent | Enable Message Content Intent in the portal; check the selected channel and send new text from a human account. |
| Startup cannot access a channel | Confirm both IDs, the server installation, and channel permissions. Use ordinary text channels. |
| Discord `Forbidden` when reporting | Allow View Channel, Send Messages, and Embed Links in the private review channel. |
| Liquid HTTP 401 / 402 / 403 / 429 | Check the API key / credit / model access / rate limit, respectively. |
| `Skipped message` | Wait for the current check to finish, then post a new test message. |

## Files and checks

```text
classifier.py                          # The question and Liquid API call
bot.py                                 # Discord events and review reports
.env.example                           # Empty configuration template
notebooks/d1_ultra_solicitation.ipynb    # Standalone API walkthrough
tests/                                 # Offline API, bot, and notebook checks
```

Run the offline checks from the repository root:

```sh
python -m unittest discover -s tests -v
```

Tests mock Liquid and Discord; they do not measure model accuracy or verify live
credentials. All included examples are synthetic. Clear notebook outputs before
sharing if you experiment with your own messages.
