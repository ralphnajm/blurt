# On removal ($1 is 0), stop the service for everyone who has it running.
if [ "$1" -eq 0 ]; then
    for user in $(loginctl list-users --no-legend 2>/dev/null | awk '{print $2}'); do
        systemctl --user -M "$user@" stop blurt.service >/dev/null 2>&1 || true
    done
fi
