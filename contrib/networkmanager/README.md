# PS4 Ethernet autoconnect

One-time setup for the experimental Baikal MTS2 driver. This optional tool is
for NetworkManager and systemd users, including Arch and CachyOS. It does not
require Omarchy or change your Wi-Fi connection.

## Quick setup

First boot a supported kernel and install its **whole matching module archive**
as described in [the boot guide](../../docs/BOOTING.md). Have Python 3,
NetworkManager, systemd, `ip` and `kmod` installed, with NetworkManager running.
A stock Arch/CachyOS kernel does not contain this driver.

New Linux psX CI bundles include `ps4-ethernet` beside `artifacts.json`. From
the extracted bundle, run:

```sh
sudo python3 ./ps4-ethernet --setup --manifest "$PWD/artifacts.json"
```

**Done:** setup installs and starts `ps4-ethernet.service`, enables it for future
boots, and creates a NetworkManager Ethernet profile with IPv4 DHCP and
autoconnect enabled. With a connected cable, addressing proceeds automatically.
The service prepares the driver before NetworkManager brings up the connection.
Wi-Fi stays configured; the Ethernet profile uses IPv4 route metric 100.

For an older bundle without the script, download it first:

```sh
curl -fL https://raw.githubusercontent.com/meerzulee/linux-psx/main/contrib/networkmanager/ps4-ethernet -o ps4-ethernet
```

Use the metadata **from your exact kernel/module build**:

| Running kernel (`uname -r`) | Metadata for `--manifest` |
| --- | --- |
| `6.18.44-psx-ps4-baikal1` | `artifacts.json` from the same Linux psX CI artifact bundle. |
| `6.18.44-ps4-baikal-mts2-public1` | `ethernet-candidate.json` from the same earlier public MTS2 release. |
| `6.18.44-ps4-baikal-mts2` | Your existing original MTS2 `ethernet-candidate.json`; typically `/etc/omarchy-ps4/ethernet-candidate.json`. |

For example, in an extracted earlier public MTS2 release:

```sh
sudo python3 ./ps4-ethernet --setup --manifest "$PWD/ethernet-candidate.json"
```

On an existing Omarchy installation that already enables the older
`omarchy-ps4-ethernet.service`, disable its **future** startup after the new
setup succeeds, so only the new adapter starts next boot:

```sh
sudo systemctl disable omarchy-ps4-ethernet.service
```

The command does not stop the current driver or connection. This migration
step has already been completed on the development PS4.

The tool rejects other kernels and mismatched config/module identities. It
installs the script at `/usr/local/sbin/ps4-ethernet`, root-private metadata at
`/etc/psx/ethernet-<kernel-release>.json`, and its service under
`/etc/systemd/system/`. It preserves an existing identical installation and
refuses to overwrite different files. Future kernel releases need their own
reviewed contract; a service pinned to an earlier release is skipped.

## Check the connection

```sh
nmcli device status
ip -br address show dev psx0
ip -4 route get 1.1.1.1
systemctl status ps4-ethernet.service --no-pager
```

For startup logs:

```sh
sudo journalctl -b -u ps4-ethernet.service --no-pager
```

DHCP/carrier are asynchronous; a successful service alone does not prove
packet traffic. See [Ethernet verification](../../docs/ETHERNET.md) for
interface-bound traffic checks and the current hardware scope.

## Manual mode and disabling future startup

To stage files without enabling a service or activating the driver:

```sh
sudo python3 ./ps4-ethernet --install --manifest "$PWD/artifacts.json"
```

Then explicitly activate once for the current boot when ready:

```sh
sudo /usr/local/sbin/ps4-ethernet --activate
```

To disable automatic activation on **future boots**:

```sh
sudo systemctl disable ps4-ethernet.service
```

This leaves the current driver and network connection running. Do not unload,
reset, unbind or rearm a published MTS2 module. The tool permits one initial
publication attempt per boot; a healthy already-published instance is recognized
without another attempt. On an activation fault, preserve the service log and
driver report rather than restarting the service or rerunning old experiments.

## Validation and license

Host tests cover artifact checks, all three release contracts, installation,
startup ordering, healthy repeat calls and refusal to retry uncertain state.
Same-boot service adoption and hash-checked wired transfers passed on the
original MTS2 kernel; see the [test record](../../docs/ETHERNET-AUTOCONNECT-TEST.md).
The new service still needs an observed unattended boot test on PS4; cable
recovery and additional console revisions remain unqualified. It does not
change the kernel patch or establish new hardware acceptance.

The single-file tool preserves its original **MIT license** and Omarchy PS4
contributors' notice. The kernel's existing licenses are unchanged. Source
inputs and adaptations are recorded in
[the import manifest](../../provenance/ethernet-activation-import.json).
