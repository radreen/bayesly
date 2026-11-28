#!/data/data/com.termux/files/usr/bin/bash
# Bayesly footage helper for an Android phone (Termux). Started by Termux:Boot or by hand:
#   bash ~/bayesly/scripts/phone-helper.sh
# Every 5 minutes: update the code, answer open live-footage requests, sleep. Logs to ~/bayesly-helper.log
cd "$HOME/bayesly" || exit 1
termux-wake-lock 2>/dev/null          # keep the CPU awake while the screen is off
while true; do
  git pull --quiet --ff-only 2>/dev/null
  python -m bayesly footage-helper --once >> "$HOME/bayesly-helper.log" 2>&1
  tail -n 400 "$HOME/bayesly-helper.log" > "$HOME/.bh.tmp" && mv "$HOME/.bh.tmp" "$HOME/bayesly-helper.log"
  sleep 300
done
