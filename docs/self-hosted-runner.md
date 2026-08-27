# Setting up the India runner

**What this does:** lets GitHub run the nightly collection *on your computer* instead of on
its own servers, so requests come from your Indian internet connection.

**Why it's needed:** Cleartrip serves an Indian IP normally, but returns HTTP 403 — even for
`robots.txt` — to a datacentre in the US. That's not Cleartrip blocking scrapers; it's
Cleartrip refusing to talk to foreign servers at all. Your own prototype proved this by
collecting real fares from the same URLs that GitHub's servers can't reach.

Nothing else changes. Same schedule, same robots.txt checking, same rate limiting, same
audit trail, same commits to the same repository. **Only the geography moves.**

You keep the cloud collector too. It handles EaseMyTrip every night whether your computer
is on or not, so a night when your PC is asleep costs you one source, not the whole day.

---

## One-time setup — about 10 minutes

### 1. Open the runner page

Go to:
**https://github.com/Asmitrawat4078/apix-airfare-index/settings/actions/runners**

Click the green **New self-hosted runner** button, then choose **Windows** and **x64**.

### 2. Follow GitHub's commands

GitHub shows you a block of PowerShell commands with a token already filled in. Open
**PowerShell** and run them one at a time. They will:

- make a folder called `actions-runner`
- download the runner program
- unzip it
- run `.\config.cmd` to connect it to your repository

**Use the commands GitHub shows you, not commands from anywhere else** — they contain a
token that's unique to you and expires after an hour.

### 3. Answer the config questions

`.\config.cmd` asks four things:

| Question | Answer |
|---|---|
| Enter the name of the runner group | press **Enter** |
| Enter the name of this runner | press **Enter** |
| **Enter any additional labels** | type **`india`** and press Enter ← **this one matters** |
| Enter name of work folder | press **Enter** |

That `india` label is how the workflow finds your machine. Without it the nightly job will
queue forever waiting for a runner that doesn't exist.

### 4. Install it as a service so it survives restarts

Still in PowerShell, in the same folder:

```powershell
.\svc.sh install
.\svc.sh start
```

If those don't work on your Windows version, run this instead:

```powershell
.\run.cmd
```

…but that only lasts until you close the window. **The service version is what you want** —
it starts automatically with Windows and keeps working after a reboot.

### 5. Check it worked

Back on the runners page you should see your computer listed with a green **Idle** dot and
an `india` label. That's it.

---

## Then test it

I've set up a test that writes nothing and just confirms the runner is alive and Cleartrip
answers. Tell me when the runner shows Idle and I'll trigger it.

---

## What you should know before committing to this

**Your computer needs to be on and awake at 02:40 IST.** If it's asleep the job queues, and
after 180 minutes it gives up and that night's Cleartrip data is gone permanently. Fares
cannot be re-collected.

Two practical fixes, either is fine:

- **Change the schedule to a time you're normally at your desk.** There's nothing sacred
  about 2am — what matters is that it's *the same time every day*, because comparing a
  fare collected at 2am against one collected at 6pm measures the intraday cycle rather
  than inflation. Pick a time and never move it. Tell me and I'll change it.
- **Stop Windows sleeping at night.** Settings → System → Power → Screen and sleep → set
  "When plugged in, put my device to sleep after" to **Never**.

**This is why the cloud collector stays on.** EaseMyTrip works from GitHub's servers and
runs regardless of your laptop. Your PC is the *second* source, not the only one.

---

## A note worth putting in the deck

Running the same basket from two countries on the same night is not just an engineering
workaround — it's a small piece of original research. If quoted fares differ systematically
by where the request comes from, that's a real finding about point-of-sale pricing, and
it's directly relevant to a statistical agency deciding where to collect from. Nobody else
in this problem statement will have that comparison, and you'll get it for free every night.

---

## If something goes wrong

**Runner shows "Offline"** — your PC is off, asleep, or the service stopped. Restart it:
`.\svc.sh start` in the `actions-runner` folder.

**Job says "waiting for a runner"** — the `india` label is missing. Re-run `.\config.cmd`
and add it, or remove the runner and set it up again.

**Cleartrip starts refusing you too** — that's the site's answer and we take it. The
collector records those cells as `blocked`, imputation covers them, and the availability
rate makes it visible. We do not work around it.

**Runner stops after a Windows update** — normal. Start the service again.
