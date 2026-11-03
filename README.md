<p align="center">
  <img src="assets/liquid-logo.png" alt="Liquid AI" width="200">
</p>

# Discord moderation bot with `d1-ultra`

Build a bot that asks one question: **is this message unsolicited solicitation?**

It reads new messages in selected text channels of one server and detects sales,
recruitment, or signup pitches. It saves a report in a private moderator channel,
then deletes text messages with a solicitation score of at least `0.99`. Lower
scores from `0.80` are reported for human review. 

```text
Message → d1-ultra → below 0.80: no action
                  → 0.80–below 0.99: private review report
                  → 0.99 or above: archive first, then delete eligible text messages
```

This is a small piece of the original bot's spam/advertising rule. Project demos,
technical questions, and scam warnings should be allowed; a link alone isn't a
violation. We assume unsolicited promotion is not allowed in the selected channels.

**Just want to try d1-ultra?** Open the [notebook](notebooks/d1_ultra_solicitation.ipynb).
It walks through the API independently of Discord.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Liquid4All/d1-discord-mod-tutorial/blob/main/notebooks/d1_ultra_solicitation.ipynb)

## 1. Create a virtual environment

You need Python 3.11+ (3.12 recommended), a Liquid API key with available credit,
and a Discord test server where you have **Manage Server** permission.

Get the code and enter its folder:

```sh
git clone https://github.com/Liquid4All/d1-discord-mod-tutorial.git
cd d1-discord-mod-tutorial
```

Create a local `.venv` once, then activate it. This keeps the project's Python
packages separate from your other projects.

**macOS / Linux:**

```sh
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the dependencies into the active environment:

```sh
python -m pip install -r requirements.txt
```

When you open a new terminal, return to this folder and run the activation command
again. You only need to create the environment once. Run `deactivate` when finished.

Copy `.env.example` to a new file named `.env` for your keys and settings.
`.venv` holds installed packages; `.env` holds configuration. Both are ignored by
Git. Leave the example file empty.

## 2. Get your API key and bot token

**Liquid:** open [Liquid Console](https://console.liquid.ai), select your
organization, then go to **Dashboard → API Keys** and create a key.

**Discord:** open the [Developer Portal](https://discord.com/developers/applications):

1. Create a **New Application** named `d1-mod-tutorial`.
2. Open **Bot → Reset Token** and copy the token.
3. Enable **Message Content Intent** on the Bot page and save.
4. Under **Installation**, enable **Guild Install**, select **Discord Provided
   Link**, and add the **bot** scope to the Guild Install defaults.
5. Select **View Channels**, **Read Message History**, **Manage Messages**,
   **Send Messages**, **Embed Links**, and **Attach Files** permissions.
6. Open the install link, choose **Add to server**, and select your test server.

**Manage Messages** lets the bot delete other users' messages; Administrator is
not needed. It will appear offline until you run it. If the bot is already
installed, update its server role and channel permissions as described below.

## 3. Choose the channels to monitor

Choose one or more text channels to monitor, such as `#general` and `#show-your-work`.
Create a **private** `#mod-review` text channel in the same server. Give the bot
**View Channel**, **Read Message History**, and **Manage Messages** in each monitored
channel. In `#mod-review`, give it **View Channel**, **Send Messages**, **Embed Links**,
and **Attach Files**. Keep the review channel visible only to moderators and the bot.

In Discord, enable **User Settings → Advanced → Developer Mode**. Right-click
each channel and choose **Copy Channel ID**. Fill in your local `.env`:

```dotenv
LIQUID_API_KEY=your_liquid_key_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_IDS=111111111111111111,222222222222222222
DISCORD_REVIEW_CHANNEL_ID=your_private_mod_review_channel_id
```

Replace the example numbers with your monitored channels' IDs, separated by commas.
Use one numeric ID for the private review channel and keep it out of that list.
All channels must be in the same server; the bot checks this at startup.

Coming from the original bot? Reuse `DISCORD_LOG_CHANNEL_ID` as
`DISCORD_REVIEW_CHANNEL_ID`. `DISCORD_GUILD_ID` is no longer needed because the
channels identify the server. If you used this tutorial's earlier
`DISCORD_CHANNEL_ID` setting, rename it to **`DISCORD_CHANNEL_IDS`** and add more IDs.

## 4. Understand the decision

[classifier.py](classifier.py) uses `TypeSafeClient` and `Noul`, following
[Liquid's Python SDK example](https://docs.liquid.ai/lfm/models/d1#setup):

```python
from typesafe_sdk import Noul, TypeSafeClient

with TypeSafeClient(
    api_key=api_key,
    base_url="https://api.liquid.ai/decisions",
    compression=False,
) as client:
    result = client.system_one(
        model="d1-ultra",
        state=message,
        questions={"solicitation": Noul(instructions=INSTRUCTIONS)},
    )

probability = result.answers["solicitation"].noul
```

`state` is the message text. `INSTRUCTIONS` explains what counts as solicitation.
`Noul` asks a yes/no question; `.noul` reads the probability of yes.
The pinned SDK appends `/v1/systemone`, so the base URL includes `/decisions` to
reach Liquid's documented endpoint. `compression=False` sends ordinary JSON.
The bot also sets a timeout and disables retries to make one attempt per message.

[bot.py](bot.py) uses two thresholds from `classifier.py`:

```python
REVIEW_THRESHOLD = 0.80  # Save a private report.
DELETE_THRESHOLD = 0.99  # Also delete, after saving the report.
```

These are starting points, not accuracy guarantees. A `0.99` score does not
guarantee a correct decision. Keep the review threshold at or below the deletion
threshold. Try synthetic messages in test channels before using real conversations.

## 5. Run it

With your virtual environment active:

```sh
python bot.py
```

Wait for `Ready: watching ...; reports go to #mod-review`, listing your selected
channels. Then post these invented examples in a monitored test channel from
your own account, one at a time:

| Message | Desired result |
|---|---|
| Buy my AI consulting package. DM me for pricing. | Report; delete if score ≥ 0.99 |
| Join my business opportunity. DM to earn money. | Report; delete if score ≥ 0.99 |
| I built an open-source chatbot. Feedback is welcome. | No flag |
| How do I run this model locally? | No flag |
| Watch out for “DM me to earn money” messages — it's a scam. | No flag |

Wait for each result in the terminal before posting the next message. These are
desired results, not measured predictions; the model can make mistakes. A report
includes an excerpt, author and message IDs, source channel, timestamp, and score.
An attached JSON file preserves the **full text**, policy question, and thresholds
for post-mortem analysis. Its `planned_action` records the intention; the report's
footer shows the actual outcome.

For scores at or above `0.99`, the bot uploads that archive **before deleting**.
If the upload fails, it leaves the original untouched. It then fetches the latest
message and cancels deletion if the text or edit timestamp changed, or uninspected
media appeared. Attachments, stickers, polls, and interactive components make a
message review-only. The original-message link stops working after deletion;
use the archived JSON instead. Deletion cannot be undone; the archive preserves a
copy of the text.

Discord does not provide an atomic version-check-and-delete operation, so an edit
in the brief interval after the final check can still race with deletion. If the
report footer remains “deletion pending,” check the terminal and original message;
a pending report is not proof that deletion succeeded.

Keep the program running while testing. Press **Ctrl+C** to stop. Restart after
changing settings or code.

## Try the API notebook

Run [the notebook](notebooks/d1_ultra_solicitation.ipynb) in Colab, VS Code, or Jupyter.
Colab provides its own runtime. To run locally, activate the `.venv` from step 1,
then install Jupyter and register a kernel inside that environment:

```sh
python -m pip install jupyterlab ipykernel
python -m ipykernel install --sys-prefix --name d1-tutorial --display-name "Python (d1 tutorial)"
python -m jupyter lab notebooks/d1_ultra_solicitation.ipynb
```

Select **Python (d1 tutorial)** as the notebook's kernel. In VS Code, you can
instead choose this project's `.venv` using **Select Kernel → Python Environments**.

It needs only a Liquid API key, entered through a hidden prompt. It does not load
`.env` automatically. A full run makes seven paid requests; the final threshold
experiment reuses the results without more calls. No GPU is needed.

## Scope and troubleshooting

This example checks only new text in the selected channels. It skips bots,
webhooks, DMs, threads, edits, and old messages. It does not inspect images,
attachments, profiles, or linked pages. Messages containing attachments, stickers,
polls, or interactive components can be reported but are never automatically deleted.
Link previews are not copied; links in the message text are preserved. Each check
sends the message text and question to Liquid in one paid API request.

The bot handles one request at a time and logs a warning if another message
arrives while it is busy. Failed requests are logged without retrying. Use
quiet test channels for this tutorial.

- **Online but silent:** check Message Content Intent, the channel IDs, and send
  a new text message from a human account.
- **Can't access, archive, or delete:** check the permissions in step 3, especially
  **Manage Messages** on monitored channels and **Attach Files** in the private channel.
- **Liquid HTTP 401 / 402 / 403 / 429:** check your API key / credit / model
  access / rate limit, respectively.

References: [Liquid API](https://docs.liquid.ai/lfm/models/d1),
[Discord installation](https://docs.discord.com/developers/quick-start/getting-started),
[message intents](https://discordpy.readthedocs.io/en/stable/intents.html#message-content).

## Support

- 📖 [Liquid AI Documentation](https://docs.liquid.ai/)
- 💬 [Join our community on Discord](https://discord.com/invite/liquid-ai)
