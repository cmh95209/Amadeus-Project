# Remote access: open her on your phone

Amadeus runs on your PC. With **Tailscale** (free for personal use) you can open
her page on your phone, tablet or another computer, at home or away, while she
keeps running on the PC. Only devices signed in to **your own** Tailscale account
can reach her.

The same steps are in the app: **Settings → Mobile and remote access**.

> **Use "serve", never "Funnel".** Tailscale has two ways to share a page:
> **Serve** shares it only with your own Tailscale devices; **Funnel** puts it on
> the public internet. Amadeus has no login, so with Funnel anyone who finds the
> address could talk to her and read your chats. These steps use Serve only. If
> the settings tab ever shows a red "Funnel is ON" warning, turn it off with
> `tailscale funnel reset` (then do step 3 again).

## What you need

- Amadeus installed and running on your PC (Windows or Mac).
- A phone (Android or iPhone), tablet or second computer.
- A free Tailscale account (you can sign in with Google, Microsoft, Apple or GitHub).

## Step 1: install Tailscale on the PC and on the phone

- **PC:** download it from <https://tailscale.com/download> and install it.
- **Phone:** install "Tailscale" from the Google Play Store or the App Store.

## Step 2: sign in to the same account on both

Open Tailscale on the PC and on the phone and sign in with the **same** account.
Leave Tailscale switched on on both. In the Tailscale app on your phone you should
now see your PC in the list of devices.

## Step 3: turn on remote access on the PC

Easiest: in Amadeus open **Settings → Mobile and remote access** and press
**Set it up for me**. It shows the two commands below and asks before it runs them.

Or run them yourself in a terminal on the PC (PowerShell on Windows, Terminal on a
Mac):

```bash
tailscale serve --bg --https=443 http://localhost:5173
```

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:5050
```

The first one shares her page, the second one her "brain" (the backend the page
talks to). Both are needed.

**The first time only**, Tailscale may answer with a link and wait, because
Serve (and HTTPS certificates) must be allowed once for your Tailscale account.
Open the link, allow it, and run the commands again (or press **Set it up for me**
again).

On a Mac, if `tailscale` is not found in Terminal, use the button in Settings
instead, or turn on the command line in the Tailscale app's settings.

## Step 4: open her on your phone

Back in **Settings → Mobile and remote access** you now see a green **On**, her
address (it looks like `https://my-pc.tail1234.ts.net`) and a QR code.

- Scan the QR code with the phone's camera, or type the address in its browser.
- Tailscale must be switched on on the phone.
- Tip: in the phone browser's menu choose **Add to Home screen** / **Install app**
  to open her like an app.

The PC must be on, with Amadeus running, for the phone to reach her.

## Turning it off

In Settings press **Turn remote access off**, or run:

```bash
tailscale serve --https=443 off
```

```bash
tailscale serve --https=8443 off
```

## Common problems

| What you see | What to do |
|---|---|
| "Tailscale is not installed on this PC" | Step 1. Restart Amadeus after installing. |
| "Tailscale is installed but not running" | Open Tailscale on the PC and sign in (step 2). |
| "Tailscale is on, but ... not shared yet" | Step 3 (one of the two commands is missing). |
| The phone says the page can't be reached | Switch Tailscale on on the phone; check the PC is on and Amadeus is running. |
| The page opens but she never answers | The second command (port 8443) is missing - run step 3 again. |
| A red "Funnel is ON" warning | Run `tailscale funnel reset`, then step 3 again. |

The "Set it up for me" and "Turn remote access off" buttons only work on the PC
itself; on the phone the tab only shows the status.
