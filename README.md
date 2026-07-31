# Mirror Mirror

Mirror your PC screen to your phone over your local network, with audio and basic touchpad control. No account, no cloud relay, nothing leaves your router.

Built this because Chrome Remote Desktop is discontinued, RDP shadowing doesn't work on Windows 10/11 Pro out of the box, and every third-party remote desktop app wants an account and a subscription for something that should just work on your own Wi-Fi.

Considering an optional paid tier for remote access, [vote here](https://github.com/maco30001/Mirror-Mirror/discussions/2) if you'd use it.

## Install

Grab the latest installer from [Releases](../../releases), run it, done. No admin rights needed, it installs to your user folder and adds a Start Menu entry.

The first time it launches, Windows will ask whether to allow it through the firewall. Click **Allow**, or nothing on your network will be able to reach it.

## Support

Any additional support or requests, please email clearkeymultiservices@gmail.com

## Features

- Full desktop mirroring with audio, via Chrome's screen capture. 30fps, no noticeable lag on a normal home network
- Touchpad-style control from your phone: drag to move the cursor, tap to left-click, two-finger tap to right-click, two-finger drag to scroll
- Pairing uses a random 6-digit code, not an open room name. Codes expire after 5 minutes, can't be reused once claimed, and repeated wrong guesses get rate-limited
- Remembers devices that have connected before. Reconnecting later shows up as a one-tap option on your phone instead of retyping a code, and can be forgotten again at any time
- Automatically resumes if your phone briefly disconnects, screen lock, backgrounded tab, a wifi blip, without needing a new code
- Runs from the system tray, no console window sitting open
- Add-to-home-screen support on iOS/Android so it behaves like an installed app
- Everything stays on your LAN. No signaling server outside your network, no data leaving your house

## Requirements

- Windows 10 or 11 on the host machine
- Any modern browser on the viewing device (Safari or Chrome), on the same Wi-Fi network as the host

## Usage

1. Launch Mirror Mirror. It starts in the system tray, and a one-time popup tells you it's there
2. On the PC, open `http://localhost:5000` and click **Share Screen**
3. Right-click the tray icon to copy your network address, or find it in the app's menu
4. On your phone, open that address, type in the 6-digit code shown on the PC, and tap **View Screen**

Screen sharing has to be started from `localhost` specifically on the host. That's a browser security requirement for screen capture, not a bug.

Need a new code without stopping the share? Tap the refresh icon next to the code on the host. Anyone already connected is told the code changed and disconnected cleanly, not left staring at a frozen screen.

Once a phone has connected before, it'll show up automatically the next time you open the app there, with a one-tap **Reconnect** button instead of typing the code again.

## Roadmap

- Remote access from outside your home network, so you can reach your PC from anywhere, not just your own Wi-Fi.
- Native mobile app to replace the current browser-based install flow
- Multi-monitor selection

## License

MIT. See [LICENSE](LICENSE).
