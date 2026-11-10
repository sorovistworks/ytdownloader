# Put YouTube Downloader online for free (Oracle Cloud)

You do this once, in about 30–45 minutes. After that the site runs by itself, around the clock, with your computer off. Oracle's Always Free plan includes the server and 10 TB of downloads a month, so it costs nothing as long as you stay within those free limits (step 2 adds a safety alert).

Oracle renames buttons now and then. If a name below doesn't match exactly, look for the closest one.

## 0. Merge the code

Open https://github.com/sorovistworks/ytdownloader/pulls and merge the open pull request, so the server gets the latest version.

## 1. Create the Oracle account

1. Go to https://www.oracle.com/cloud/free/ and choose **Start for free**.
2. **Home region:** pick the one closest to your family, for example *India West (Mumbai)* or *India South (Hyderabad)*. It can't be changed later.
3. Oracle asks for a card to confirm you're a real person. Always Free resources are not charged.

## 2. Keep the server from being removed, and add a safety alert

Oracle may remove free servers that sit mostly idle, which a family site does. Upgrading the account prevents that, and free resources stay free.

1. Open the menu (☰) → **Billing & Cost Management** → **Upgrade and Manage Payment** → choose **Pay As You Go**.
2. Menu → **Billing & Cost Management** → **Budgets** → **Create Budget**. Set the amount to **1** and add an alert to your email at 100%. If anything ever starts costing money, you'll know right away.

## 3. Create the server

Menu → **Compute** → **Instances** → **Create instance**.

| Setting | Choose |
|---|---|
| Name | `youtube-downloader` |
| Image | **Change image** → **Ubuntu** → **Canonical Ubuntu 24.04** |
| Shape | **Change shape** → **Ampere** → **VM.Standard.A1.Flex**, set **2 OCPUs** and **12 GB** memory |
| Networking | Create a new virtual cloud network with a **public subnet**, and keep **Assign a public IPv4 address** on |
| SSH keys | **Generate a key pair for me** → **Save private key**. Keep this file safe. It's the key to your server. |

Press **Create**. After a minute the server shows **Running** and a **Public IP address**. Write it down.

If you see **Out of capacity**, try another *Availability domain* in the same form, or try again in a few hours. As a fallback, the shape **VM.Standard.E2.1.Micro** is also free. It's slower but works.

## 4. Open the web ports

On the server's page, click the **Subnet** link → **Security** (or **Security Lists**) → **Default Security List** → **Add Ingress Rules**:

- Source CIDR: `0.0.0.0/0`
- IP Protocol: `TCP`
- Destination Port Range: `80,443`

Press **Add Ingress Rules**.

## 5. Connect to the server

On your Windows PC, open **PowerShell** and type (use your key file's real name and your server's IP):

```
ssh -i "$HOME\Downloads\ssh-key-2026-11-10.key" ubuntu@YOUR.SERVER.IP
```

Type `yes` when asked. If it complains the key is *unprotected*, run this once and try again:

```
icacls "$HOME\Downloads\ssh-key-2026-11-10.key" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

## 6. Install the site

In that same window, now connected to the server, paste these two lines:

```
git clone https://github.com/sorovistworks/ytdownloader.git
sudo bash ytdownloader/deploy/oracle/setup.sh
```

It installs everything, asks you to choose the **family password**, and finishes with your link, something like:

```
Done! Your family's link:  https://152-67-10-20.sslip.io
```

That address is free and comes with a proper https certificate. Type `exit` to disconnect. The site keeps running.

## 7. Share it

Send your family the link and the password, plus the short guide below.

---

## For the family

1. Open the link and type the password. Your phone remembers it.
2. Paste a YouTube link, or type the name of a video and pick it from the list.
3. Choose:
   - **Best quality**: the highest resolution. If it won't play, open it in VLC.
   - **Plays everywhere**: works on every phone and TV.
   - **Audio**: just the sound.
4. Tap **Download**. You can leave the page; when you come back, the download carries on.

**Make it an app:** iPhone: Share → *Add to Home Screen*. Android: menu (⋮) → *Install app*. On Android you can then tap **Share** in the YouTube app and choose **Downloader**.

---

## Looking after it

Connect as in step 5 for any of these.

| Task | Command |
|---|---|
| Change the password | `sudo bash /opt/ytdownloader/deploy/oracle/setup.sh` |
| See what's happening | `cd /opt/ytdownloader/deploy/oracle && sudo docker compose logs --tail 50` |
| Restart the site | `cd /opt/ytdownloader/deploy/oracle && sudo docker compose restart` |

**Updates are automatic.** Every night at 04:30 (server time, UTC) the server fetches the latest app and the newest downloader engine, so it keeps up with YouTube's changes.

**If the family sees "YouTube is blocking this server":**

1. In a browser, sign in to YouTube with a spare Google account (not your main one).
2. Export its cookies as `cookies.txt`, for example with the "Get cookies.txt LOCALLY" extension.
3. From PowerShell on your PC, copy it to the server:
   ```
   scp -i "$HOME\Downloads\ssh-key-2026-11-10.key" cookies.txt ubuntu@YOUR.SERVER.IP:/tmp/
   ssh -i "$HOME\Downloads\ssh-key-2026-11-10.key" ubuntu@YOUR.SERVER.IP "sudo mv /tmp/cookies.txt /opt/ytdownloader/deploy/oracle/secrets/cookies.txt"
   ```

It takes effect from the next download. No restart needed.
