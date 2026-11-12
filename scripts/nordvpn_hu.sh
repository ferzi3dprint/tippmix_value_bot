#!/usr/bin/env bash
# NordVPN (OpenVPN) csatlakozás egy ajánlott MAGYAR szerverre a GitHub-gépen.
# Titkok: NORD_USER, NORD_PASS = a Nord-fiók „service credentials” (kézi beállítás) adatai, NEM a belépési jelszó.
set -euo pipefail
[ -n "${NORD_USER:-}" ] && [ -n "${NORD_PASS:-}" ] || { echo "Nincs NORD_USER / NORD_PASS titok – VPN kihagyva."; exit 1; }
sudo apt-get -qq update >/dev/null && sudo apt-get -qq install -y openvpn >/dev/null
HU_ID=$(curl -s "https://api.nordvpn.com/v1/servers/countries" | python3 -c "import sys,json; print(next(c['id'] for c in json.load(sys.stdin) if c['code']=='HU'))")
HOST=$(curl -s "https://api.nordvpn.com/v1/servers/recommendations?filters\[country_id\]=${HU_ID}&filters\[servers_technologies\]\[identifier\]=openvpn_udp&limit=1" \
       | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['hostname'])")
echo "Magyar Nord-szerver: $HOST"
curl -s -o /tmp/hu.ovpn "https://downloads.nordcdn.com/configs/files/ovpn_udp/servers/${HOST}.udp.ovpn"
printf '%s\n%s\n' "$NORD_USER" "$NORD_PASS" > /tmp/nord.auth && chmod 600 /tmp/nord.auth
sudo openvpn --config /tmp/hu.ovpn --auth-user-pass /tmp/nord.auth --daemon --log /tmp/openvpn.log
for i in $(seq 1 30); do
  sleep 2
  C=$(curl -s --max-time 5 https://ipinfo.io/country || true)
  if [ "$C" = "HU" ]; then echo "VPN kész – kimenő IP országa: HU"; exit 0; fi
done
echo "A VPN nem állt fel magyar IP-vel. Napló:"; sudo tail -30 /tmp/openvpn.log; exit 1
