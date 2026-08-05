# -333-333-333-

Owner-only mirror of the complete trading stack, fully separated from the
original ecosystem. See `ISOLATION.md` for the separation rules and layout.

New Android client: `terminal333-android/` ("Terminal 333",
`com.lab333.terminal`) — build with JDK 21:

```sh
cd terminal333-android
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ./gradlew assembleRelease
```

Signing keystore + passwords: `~/lab333/secrets/` (never in this repo).
