# PS4 Ethernet autoconnect

One-time setup for the experimental Baikal MTS2 driver. This optional tool is
for NetworkManager and systemd users, including Arch and CachyOS. It does not
require Omarchy or change your Wi-Fi connection.

## Quick setup

First boot a supported kernel and install its **whole matching module archive**
as described in [the boot guide](../../docs/BOOTING.md). Have Python 3,
NetworkManager, systemd, `ip` and `kmod` installed, with NetworkManager running.
A stock Arch/CachyOS kernel does not contain this driver.

Download the script and run setup:

```sh
curl -fL https://raw.githubusercontent.com/meerzulee/linux-psx/master/contrib/networkmanager/ps4-ethernet -o ps4-ethernet
sudo python3 ./ps4-ethernet --setup
```

Setup detects your running kernel, finds its matching build data locally or
fetches it from the same public release, then enables Ethernet now and at
future boots. **You do not need to find, download or specify a JSON file.**
NetworkManager handles IPv4 DHCP and autoconnect; Ethernet uses route metric
100 while Wi-Fi stays configured.

**Offline setup:** the small CI kernel download already contains the script
and matching data. Extract it and run `sudo python3 ./ps4-ethernet --setup` there.

## Kernel downloads

The existing public MTS2 Ethernet tester build is available without a GitHub
account:

https://github.com/meerzulee/linux-ps4/releases/tag/v6.18.44-ps4-baikal-mts2-public1

Get `bzImage` and its complete matching modules archive and follow that build's
boot guide. The setup script automatically downloads its small matching build
record when needed. The newer psX builds are currently CI downloads:

https://github.com/meerzulee/linux-psx/actions/workflows/kernel.yml

For CI, open a completed successful run, sign in to GitHub, download
**`linux-psx-ps4-baikal-<commit>`** and extract it. The larger
`linux-psx-ps4-source-<commit>` is a separate source download for developers.
The small kernel download includes its build data. A standalone psX metadata
download becomes available when that exact build is published as a release;
unpublished or custom builds need their original bundle. Setup never invents
checksums or substitutes another kernel's data.

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

## Advanced options and existing Omarchy installations

Automatic discovery checks installed root-private build data, the original
Omarchy MTS2 record when applicable, the script's folder and current folder,
then the exact public release. An existing invalid record is refused.

| Running kernel (`uname -r`) | Build data used automatically |
| --- | --- |
| `6.18.44-psx-ps4-baikal1` | Bundle `artifacts.json`, installed record, or the same public psX release. |
| `6.18.44-ps4-baikal-mts2-public1` | Bundle record or the existing public MTS2 release. |
| `6.18.44-ps4-baikal-mts2` | Installed record or original `/etc/omarchy-ps4/ethernet-candidate.json`; private builds have no public fallback. |

An explicit `--manifest /path/to/build-record.json` remains available for a
custom location. To stage files without activation or startup changes, use
`sudo python3 ./ps4-ethernet --install`; later activate with
`sudo /usr/local/sbin/ps4-ethernet --activate`.

On an existing Omarchy installation, after new setup succeeds, disable the
older duplicate's future startup with
`sudo systemctl disable omarchy-ps4-ethernet.service`. This migration is already
complete on the development PS4. The command leaves the current driver running.

To disable new automatic activation on future boots:

```sh
sudo systemctl disable ps4-ethernet.service
```

This leaves the current connection running. Do not unload, reset, unbind or
rearm a published MTS2 module. The tool permits one initial publication attempt
per boot and recognizes a healthy initialized instance without another attempt.
On an activation fault, preserve service logs and the driver report.

## Validation and license

Host tests cover artifact checks, release contracts, local discovery, anonymous
HTTPS download, installation, startup ordering and retained-state guards. The
public MTS2 metadata URL was also fetched anonymously and verified successfully.
A previous adapter revision passed same-boot service adoption and hash-checked
wired transfers on the original MTS2 kernel; see the
[test record](../../docs/ETHERNET-AUTOCONNECT-TEST.md). The new discovery path is
host-tested. Unattended startup and cable recovery remain unqualified.

The single-file tool preserves its **MIT license** and Omarchy PS4 contributors'
notice. The kernel's licenses are unchanged. Source inputs and adaptations are
recorded in [the import manifest](../../provenance/ethernet-activation-import.json).
