#!/bin/bash
# Remove the InterDAW helper and its login item. Your Live sets, LUNA sessions and anything
# Send Back made are not touched.
#   curl -fsSL https://github.com/cp666-dev/interdaw/releases/latest/download/uninstall-helper.sh | bash
set -uo pipefail

LABEL="com.cp.interdaw.helper"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Library/Application Support/InterDAW" "$HOME/Library/Logs/InterDAW"
echo "The InterDAW helper is removed."
echo "To remove the Add On too, open LUNA's Settings > Add Ons and remove InterDAW."
