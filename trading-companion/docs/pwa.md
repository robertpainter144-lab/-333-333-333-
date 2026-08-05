# Installing the PWA

The family member doesn't install anything from an app store. They open the
URL in their phone's browser and "Add to Home Screen."

## iPhone (Safari)

1. Open `https://companion.example.com` in Safari.
2. Tap the Share button (square with up arrow at the bottom).
3. Scroll the share sheet and tap **Add to Home Screen**.
4. Name it (default "Companion") and tap Add.
5. An icon appears on the home screen. Tapping it launches the app
   full-screen, with no Safari chrome.

The first time the app opens, the onboarding screen asks for the backend URL
and API token Robert shared.

### iPhone caveats

- Must use **Safari** for the install step; Chrome on iOS can't install PWAs.
- The icon shows up even if you skip step 4, but tapping it launches Safari
  with chrome instead of the standalone shell.
- Notifications work on iOS 16.4+ but require user permission and an HTTPS
  origin.

## Android (Chrome)

1. Open the URL in Chrome.
2. Chrome shows an "Install" prompt at the bottom — tap it.
3. App icon lands on the home screen and the app drawer.

If the prompt doesn't appear, use the three-dot menu → **Install app**.

## Desktop (testing on the Mac)

1. `cd web && npm install && npm run dev`
2. Open `http://localhost:5173` in any browser. The full PWA UI loads.
3. To test the production build:
   ```
   npm run build
   npm run preview
   ```
   Then open `http://localhost:4173`.

## Updating the app

Every time you push a new build to the server, the PWA picks it up on the next
page load. No store reviews, no re-signing. The family member may need to
swipe-close the app and re-open to get the new bundle if they have it open.

## When to revisit

- If iOS Safari ever drops the in-app browser flow for IBKR auth → switch to
  native SwiftUI (the scaffold in this repo's history can be revived).
- If Apple ever makes PWA installs friendlier than "Add to Home Screen" buried
  in the share sheet → no change needed; we get it for free.
