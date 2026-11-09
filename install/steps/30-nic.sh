# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Step 30: the bus network card. IgH master 0 on that card with the generic
# driver (/etc/ethercat.conf), the card kept away from NetworkManager and
# systemd-networkd (no DHCP on the bus), set up before the master starts;
# ethercat.service enabled and started.
say "Bus network card"

nic_mac() { cat "/sys/class/net/$1/address"; }
default_route_if() { ip route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' | head -1; }
ethernet_cards() {        # physical, wired: a device behind it, Ethernet type, not wireless
    local d
    for d in /sys/class/net/*; do
        [ -e "$d/device" ] && [ ! -e "$d/wireless" ] && [ "$(cat "$d/type")" = 1 ] && printf '%s\n' "${d##*/}"
    done
}

nic_step() {
    if [ "$KEEP_IGH" = 1 ] && [ -z "$NIC" ]; then
        info "--keep-igh: /etc/ethercat.conf and the ethercat service left as they are"
        return 0
    fi
    local uplink current='' cards card
    uplink=$(default_route_if)
    if [ -r /etc/ethercat.conf ]; then
        current=$(sed -n 's/^MASTER0_DEVICE="\{0,1\}\([0-9A-Fa-f:]*\)"\{0,1\}.*/\1/p' /etc/ethercat.conf | tr 'A-F' 'a-f')
    fi

    if [ -z "$NIC" ] && [ -n "$current" ]; then      # a re-run keeps the card already chosen
        for card in $(ethernet_cards); do
            [ "$(nic_mac "$card")" = "$current" ] && NIC=$card
        done
        [ -n "$NIC" ] && info "keeping $NIC ($current), already in /etc/ethercat.conf"
    fi
    if [ -z "$NIC" ]; then
        mapfile -t cards < <(ethernet_cards | grep -vx "${uplink:-none}")
        if [ "${#cards[@]}" = 0 ]; then
            die "no wired network card free for the bus (${uplink:-none} carries the default route). Connect one, or name it with --nic."
        elif [ "${#cards[@]}" = 1 ] && [ "$ASSUME_YES" = 1 ]; then
            NIC=${cards[0]}
        elif [ "$ASSUME_YES" = 1 ]; then
            die "several cards could be the bus (${cards[*]}): choose one with --nic"
        else
            info "Wired cards that could be the bus:"
            for card in "${cards[@]}"; do
                info "  $card  $(nic_mac "$card")  link: $(cat "/sys/class/net/$card/carrier" 2>/dev/null || echo down)"
            done
            read -r -p "   Bus card: " NIC </dev/tty
        fi
    fi
    [ -e "/sys/class/net/$NIC" ] || die "no network card $NIC"
    if [ "$NIC" = "$uplink" ]; then
        warn "$NIC carries the default route: as the bus card it stops working as a network connection"
        if [ "$ASSUME_YES" = 1 ] || ! ask "Use $NIC for the bus anyway?"; then
            die "not using $NIC for the bus (choose another card with --nic)"
        fi
    fi
    local mac
    mac=$(nic_mac "$NIC")
    info "bus card $NIC ($mac), generic driver"

    # /etc/ethercat.conf (installed by step 20): master 0 on that card.
    run sed -i -e "s/^MASTER0_DEVICE=.*/MASTER0_DEVICE=\"$mac\"/" \
               -e 's/^DEVICE_MODULES=.*/DEVICE_MODULES="generic"/' /etc/ethercat.conf

    # No network manager touches the bus card.
    if [ -d /etc/NetworkManager ]; then
        write_file /etc/NetworkManager/conf.d/90-odycat-ethercat.conf 0644 <<CONF
# The EtherCAT bus card (OdyCAT installer): not managed by NetworkManager.
[keyfile]
unmanaged-devices=mac:$mac
CONF
        systemctl is-active -q NetworkManager && run systemctl reload NetworkManager
    fi
    if systemctl is-enabled -q systemd-networkd 2>/dev/null; then
        write_file /etc/systemd/network/05-odycat-ethercat.network 0644 <<CONF
# The EtherCAT bus card (OdyCAT installer): not managed by systemd-networkd.
# 05- sorts before netplan's 10-netplan-*, so this match wins.
[Match]
MACAddress=$mac

[Link]
Unmanaged=yes
CONF
        run networkctl reload
    fi
    if grep -qsE "^[[:space:]]*(auto|allow-hotplug|iface)[[:space:]]+$NIC([[:space:]]|$)" /etc/network/interfaces /etc/network/interfaces.d/*; then
        warn "$NIC is configured in /etc/network/interfaces: remove it there, or ifupdown will configure the bus card"
    fi

    # The card up (and its link awaited) before the master starts.
    run install -D -m 0755 "$HERE/files/ethercat-link-up" /usr/local/lib/odycat/ethercat-link-up
    write_file /etc/systemd/system/ethercat.service.d/50-odycat-link-up.conf 0644 <<CONF
# OdyCAT installer: the generic driver needs the bus card up.
[Unit]
After=network.target

[Service]
ExecStartPre=/usr/local/lib/odycat/ethercat-link-up $mac
CONF
    run systemctl daemon-reload
    run systemctl enable -q ethercat
    run systemctl restart ethercat
}
nic_step
