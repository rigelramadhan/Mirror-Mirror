# Mirror Mirror — hardened LAN edition

Share a Windows PC screen with Safari on your iPhone over your home network.
Audio and relative touchpad controls are supported. No account or cloud media
relay is required. This fork adds encrypted phone connections and explicit PC
approval to the original project by [maco30001](https://github.com/maco30001/Mirror-Mirror).

## Run the Windows executable

Extract the hardened ZIP and run `MirrorMirrorHardened.exe`. Python is bundled.
The app runs in the system tray as your normal Windows user. No automatic startup
entry or system certificate trust is installed.

On the PC, open **http://localhost:5000** in Chrome or Edge. This HTTP listener
binds only to `127.0.0.1`; it is not available to other computers. The phone uses
the **https://YOUR-PC-IP:5443** address shown in the PC page and tray menu.
Allow the app through Windows Firewall only on a trusted Private network;
preferably restrict the rule to your iPhone's IP. Never port-forward either port.

## One-time iPhone certificate setup

Local IP addresses cannot use a normal public HTTPS certificate. On first launch,
the app generates a separate certificate authority and TLS identity on **your
PC**. It never shares private keys with a viewer or includes keys in its ZIP.
There is no unencrypted LAN fallback and no automatic certificate installation.

1. In the PC page, open **iPhone HTTPS setup**, then download the public
   `MirrorMirror-iPhone.mobileconfig` profile.
2. Transfer that profile directly to your iPhone using a trusted method, such as
   your own iCloud Drive or a cable. Do not obtain a replacement certificate from
   an unverified network page or bypass a Safari security warning.
3. Open the profile on the iPhone and install it in **Settings → General → VPN &
   Device Management**. This profile contains one public root certificate; it
   does not enroll your phone in MDM or configure a VPN.
4. In **Settings → General → About → Certificate Trust Settings**, enable full
   trust for the matching **Mirror Mirror Local …** certificate. Verify its name
   and SHA-256 fingerprint against the PC page before trusting it.
5. Open the HTTPS phone address in Safari. Certificate validation must succeed
   without bypassing any browser warning.

**Trusting a root certificate is a security decision.** It adds a certificate
authority to your phone. The generated root has constraints for local names and
private addresses, but do not rely on every client enforcing those constraints.
Protect this PC and its private keys, and remove the profile from your iPhone
when you stop using Mirror Mirror. We do not install or trust anything for you.
[Apple's manual certificate trust instructions](https://support.apple.com/102390).

Private keys are stored under `%LOCALAPPDATA%\MirrorMirror\tls`, with directory
permissions restricted to the current Windows account and SYSTEM. Never share
that directory. Only the **public profile downloaded from the PC page** is meant
to be transferred. Roots persist across launches; server certificates renew for
90 days and include the PC's current IP. Restart after an IP change. A damaged
identity causes startup to fail rather than silently replacing the trusted root.

## Share and control

1. On the PC page, click **Share Screen**, select the screen/window, and note the
   random six-digit code. Codes expire after five minutes.
2. On the same Wi-Fi, open the HTTPS phone address in Safari, enter the code, and
   tap **View Screen**. The viewer must wait for approval.
3. Back on the PC, check the pending connection and choose **Allow view only**,
   **Allow view + mouse**, or **Deny**. Approve only a request you just initiated
   on your own phone. Requests time out after one minute.
4. With mouse permission: drag one finger to move the pointer, tap to left-click,
   two-finger tap to right-click, or two-finger drag to scroll. There is no keyboard
   input or click-and-drag support in this source version.
5. **Disconnect**, the tray's **Stop sharing now**, or **Quit** revokes access.
   A network disconnect also ends the session; reconnecting needs a new code and
   PC approval. Old remembered-device credentials and automatic resumes are disabled.

Sessions expire after **15 minutes without mouse activity**, including view-only
sessions, or **60 minutes total**. Moving the actual PC pointer into a corner
triggers PyAutoGUI's fail-safe on the next remote mouse command and ends the
session. Use the tray stop action to revoke immediately.

## Security scope

- Host sessions require loopback access, literal localhost Host names, and a
  random local capability. Spoofed forwarded headers do not establish authority.
- Phone pages, pairing, signaling, credentials, and mouse input use HTTPS/WSS.
  Host and viewer credentials are separate and not forwarded in signaling.
- A viewer receives no credential or media offer until the PC approves it.
  View-only approval does not authorize mouse input. Disconnects revoke approvals.
- Browser Socket.IO JavaScript is bundled locally. There is no CDN fetch, Google
  STUN configuration, or automatic GitHub update check. Media uses LAN candidates.
- Input is validated, pairing requests are rate-limited, and session transitions
  are serialized. TLS requires version 1.2 or newer. Private keys are per PC.

These changes are not a full security audit or a guarantee against compromise.
A compromised PC, approved phone, browser, or dependency can still defeat the
security model. The executable is unsigned. Live iPhone screen sharing must still
be verified on your own device. Reverse proxies and public internet hosting are
not supported. **Older upstream installers and the earlier patched EXE do not
contain these hardening changes.**

## Develop, test, and build

With Python installed on Windows:

```powershell
git clone https://github.com/rigelramadhan/Mirror-Mirror.git
cd Mirror-Mirror
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Tests mock desktop automation and do not capture a screen or send real input.
They cover approval and denial, role separation, view-only controls, expiry,
revocation, malformed input, local certificate export, certificate validation,
and real HTTPS polling and WSS connections on isolated loopback ports. TLS tests
create temporary identities and protect their directories; no system trust is
changed. CI runs on Windows with Python 3.12.

To run the desktop app from source, install `requirements.txt` and run
`webrtc_browser.py`. To build the portable Windows executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1
```

The ZIP is `dist/MirrorMirror-hardened-win64.zip`; it includes instructions,
licenses, an environment manifest, source revision, and the EXE's SHA-256 checksum.
The build script lists packaged files explicitly and excludes generated identities.

## License

MIT; see [LICENSE](LICENSE). Vendored Socket.IO is MIT licensed; see
[its license](static/vendor/socket.io-LICENSE.txt) and [provenance](static/vendor/README.md).
