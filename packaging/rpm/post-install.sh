# On upgrade ($1 is 2), restart the service for everyone who has it running.
if [ "$1" -gt 1 ]; then
    for user in $(loginctl list-users --no-legend 2>/dev/null | awk '{print $2}'); do
        systemctl --user -M "$user@" daemon-reload >/dev/null 2>&1 || true
        systemctl --user -M "$user@" try-restart blurt.service >/dev/null 2>&1 || true
    done
fi
