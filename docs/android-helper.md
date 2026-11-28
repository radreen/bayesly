# Live footage helper on an old Android phone

YouTube refuses GitHub's servers ("Sign in to confirm you're not a bot"), and we don't work around
that. From a home Wi-Fi the same download works normally, so a phone at home fetches the clips.

How it works:
- A visual story (the draft has a `footage_query`, for example "GTA 6 PC gameplay") goes to Threads at once.
- Instagram and X wait up to 30 minutes, and GitHub leaves a request in `ci-state/footage_requests.json`.
- The phone checks every 5 minutes. It finds this week's videos of the subject on YouTube, cuts 3
  live moments (Gemini drops text pages, ads and talking heads) and uploads them to
  `bayesly-media/footage/<id>/`.
- The next GitHub run builds the Reel from those clips. No clips after 30 minutes → a carousel instead.

Cost: zero. The phone uses the same free Gemini key (1 lite call per Reel).

## 1. A GitHub token for the phone (on any device)
GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
- Name `bayesly-phone`, expiration 1 year.
- Repository access: **Only select repositories** → `radreen/bayesly-media` (the media repo only).
- Permissions → Repository → **Contents: Read and write**. Nothing else.
- Copy the `github_pat_...` value.

## 2. Termux on the phone
1. Install **Termux** and **Termux:Boot** from F-Droid (https://f-droid.org). The Play Store version of Termux is outdated.
2. Open Termux and paste:
   ```
   pkg update -y && pkg install -y python ffmpeg git libyaml
   git clone https://github.com/radreen/bayesly ~/bayesly
   cd ~/bayesly && pip install -e . yt-dlp
   ```
3. Create the settings file. Type the two secrets yourself:
   ```
   nano ~/bayesly/.env
   ```
   ```
   GEMINI_API_KEY=your Gemini key (the same as on the laptop)
   MEDIA_GITHUB_TOKEN=github_pat_... (from step 1)
   MEDIA_GITHUB_REPO=radreen/bayesly-media
   ```
   Save with Ctrl+O, Enter, Ctrl+X.
4. Test one pass: `python -m bayesly footage-helper --once` (no error = fine).
5. Start on every boot:
   ```
   mkdir -p ~/.termux/boot && printf '#!/data/data/com.termux/files/usr/bin/sh\nbash ~/bayesly/scripts/phone-helper.sh &\n' > ~/.termux/boot/start.sh && chmod +x ~/.termux/boot/start.sh
   ```
   Open the Termux:Boot app once, then reboot the phone, or run `bash ~/bayesly/scripts/phone-helper.sh &` now.

## 3. Phone settings
- Settings → Apps → Termux (and Termux:Boot) → Battery → **Unrestricted / Don't optimise**.
- Keep it on the charger and on home Wi-Fi.
- Log: `tail -f ~/bayesly-helper.log`.

## 4. Switch it on
On GitHub (repo variables), `FOOTAGE_PHONE=1`. Until then the bot never waits for the phone.

To stop: `FOOTAGE_PHONE=0` (posts go out at once again), or turn the phone off. With the phone off,
Instagram/X just wait 30 minutes and then post the carousel.
