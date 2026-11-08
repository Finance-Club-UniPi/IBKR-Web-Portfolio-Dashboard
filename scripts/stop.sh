#!/usr/bin/env bash
# Stop IBKR Portfolio Dashboard
# Release ZIPs keep Compose beside the launcher; the source repo keeps it above.
cd -- "$(dirname -- "$0")" || exit 1
if [ ! -f docker-compose.yml ]; then
    cd .. || exit 1
fi
echo ""
echo " Stopping IBKR Portfolio Dashboard..."
docker compose down
echo " Done."
echo ""
