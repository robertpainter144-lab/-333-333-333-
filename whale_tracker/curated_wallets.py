"""
Curated smart-money wallet list — v2 (2026-08-09 refresh).

Sources used to build and verify this list:
  - Dune Analytics `dex.trades` unified spellbook (all-EVM-chain DEX trades):
      query 8275282 — initial 30d taker screen (>=8 trades, <=500 trades
        [bot/MEV exclusion cap], >=$200k 30d volume, >=1 trade in the last
        7d, direct_ratio [taker==tx_from] >=0.5). 60 qualifying wallets;
        top 20 by volume_usd taken as TRD candidates.
      query 8275390 — single re-verification execution run 2026-08-09
        against all 34 deduped candidates: per-address count of
        amount_usd>=10000 trades in the trailing 30 days. This is the
        source of every trade_count/volume/last_trade figure in the
        comments below.
  - Etherscan public address "Name Tag" labels.
  - Arkham Intelligence entity explorer (intel.arkm.com / arkm.com).
  - Lookonchain (X: @lookonchain) and Odaily/ChinaCryptoNews (X) coverage.
  - Community-sourced Hyperliquid wallet directory
    (cryexc.josedonato.com/hl-addresses).
  - Dated public reporting: GlobeNewswire/Blockonomi (SharpLink x Galaxy
    Digital $125M Onchain Yield Fund, announced 2026-08-07); Wintermute
    USA LLC SEC/FINRA broker-dealer registration (2026-08-06); PANews
    (DWF Labs partner Andrei Grachev wallet disclosure); Cryptopolitan /
    hl.eco (James Wynn SP500 short, 2026-08-03); TRM Labs / Coingape /
    Coinlive (Justin Sun HTX-linked wallet coverage).

Methodology: 34 candidates from three research passes (Dune taker screen,
Arkham/Etherscan entity lookups, Hyperliquid trader directory) were
deduped by address (case-insensitive) and format-validated (0x+40hex);
all 34 passed. They were then re-verified in ONE Dune execution
(query 8275390 above). TRD wallets were held to a hard >=2 verified-trade
gate. MM/VC/INS/TRE entity wallets (and the one Hyperliquid-native
trader kept, James Wynn) were NOT dropped solely for showing 0
dex.trades-taker matches: market makers, OTC/custody desks and treasury
wallets characteristically transact as the *maker* side, over
OTC/CEX rails, or off this table's chain coverage entirely — a 0 here
measures "not a public spot-DEX taker in 30d", not "dormant". Those
wallets were instead kept/dropped on their independently-dated entity
evidence (see per-entry comments). Final selection caps at 25 with no
single entity >20% (5) of the list, and a deliberate mix across
MM/TRD/VC/INS/TRE rather than an all-TRD list.

Do NOT overwrite curated_wallets.py (v1) with this file — v1 stays the
production reference until this list is reviewed; COUNTERPARTIES below
is copied unchanged from v1.
"""

# (address, label, category, source_url)
WHALES = [
    # ── TRD: verified active DEX takers (Dune query 8275390, 2026-08-09) ──
    # All 16 below cleared >=2 trades >=$10k in the trailing 30d and traded
    # within the last 7 days (freshest of the original 20-candidate screen
    # by volume_usd_30d; 6 lower-volume-but-still-verified candidates were
    # cut only to make room for MM/VC/INS/TRE diversity — see EXCLUDED below).
    ("0x1e0c79b003dace526233a4d71ae029dd2ce932e2", "Top DEX taker (30d)",        "TRD", "dune.com/queries/8275390: 257 trades, $168.4M/30d, last 2026-08-09 21:36 UTC"),
    ("0x681878dc5abaa70d182b3e1c4d6a3fd1e16922c0", "High-volume low-freq trader","TRD", "dune.com/queries/8275390: 62 trades, $151.2M/30d ($2.4M avg), last 2026-08-07"),
    ("0xbb13d96f722a2328420663ea845e0ef1eb3295a0", "Whale-size infreq trader",   "TRD", "dune.com/queries/8275390: 16 trades, $112.0M/30d ($7.0M avg), last 2026-08-05"),
    ("0xeeaa2562f89bd75d920d785aacd0c401a0105597", "Large-ticket trader",        "TRD", "dune.com/queries/8275390: 40 trades, $56.1M/30d, last 2026-08-05"),
    ("0x4659362bb53d0d7c97c136753ba512dadfe2467b", "Large-ticket trader",        "TRD", "dune.com/queries/8275390: 25 trades, $56.0M/30d, last 2026-08-08"),
    ("0xf40b6ffee727ff2f1757c355d56368751802890b", "Mid-freq, high volume",      "TRD", "dune.com/queries/8275390: 118 trades, $44.5M/30d, last 2026-08-03"),
    ("0x11897fc3db6fd87ce5b7e0c794fabe2644f9065a", "Actively trading, high vol", "TRD", "dune.com/queries/8275390: 179 trades, $43.4M/30d, last 2026-08-09"),
    ("0x795facaa76aed7c5f44a053155407199f4075139", "Actively trading, high vol", "TRD", "dune.com/queries/8275390: 219 trades, $39.7M/30d, last 2026-08-09"),
    ("0xdeda0a855d5f29491e13781fddd7ca33a88a1e02", "Active high-volume trader",  "TRD", "dune.com/queries/8275390: 238 trades, $35.7M/30d, last 2026-08-08"),
    ("0x3980daa7eaad0b7e0c53cfc5c2760037270da54d", "Active high-volume trader",  "TRD", "dune.com/queries/8275390: 238 trades, $23.4M/30d, last 2026-08-08"),
    ("0xcb9240ff4a99e086f1843229fc7fe1ba7b30f941", "High-freq active trader",    "TRD", "dune.com/queries/8275390: 428 trades, $23.0M/30d, last 2026-08-09 (freshest)"),
    ("0x5c3593481cba011737e36ded62f1797c9f6afce1", "High-freq active trader",    "TRD", "dune.com/queries/8275390: 366 trades, $22.2M/30d, last 2026-08-09"),
    ("0xfe940bfe535013a52e8e2df9644f95e3c94fa14b", "Large-ticket, active",       "TRD", "dune.com/queries/8275390: 33 trades, $22.1M/30d ($670k avg), last 2026-08-09"),
    ("0xae66f81a9ff338a8d0e68f626c3fdc4e8a60bfd1", "High-freq active trader",    "TRD", "dune.com/queries/8275390: 319 trades, $21.5M/30d, last 2026-08-06"),
    ("0x0a7bf34dec67cf73f0e4ba5ec2b252dfa431d673", "Large-ticket trader",        "TRD", "dune.com/queries/8275390: 19 trades, $20.6M/30d ($1.1M avg), last 2026-08-07"),
    ("0x324963b4a7abefb7c7117774b9784839128ed06f", "Newly-active burst trader",  "TRD", "dune.com/queries/8275390: 93 trades, $16.6M/30d, ALL trades in last 7d, last 2026-08-06"),

    # ── TRD: named individual trader (Hyperliquid perps, not spot-DEX) ────
    # 0 dex.trades-taker matches expected: Wynn's tracked size is leveraged
    # HL perp positions, a different venue/product from this table's scope.
    ("0x5078c2fbea2b2ad61bc840bc023e35fce56bedb6", "James Wynn",                 "TRD", "cryexc.josedonato.com HL directory; Cryptopolitan/hl.eco: ~$530.9k SP500 50x short held as of 2026-08-03. 0 dex.trades-taker matches (perps, not spot swaps)."),

    # ── MM: market makers ──────────────────────────────────────────────
    ("0x51c72848c68a965f66fa7a88855f9f7784502a7f", "Wintermute",                 "MM",  "Arkham+Etherscan label 'Wintermute: Market Maker'; corroborated by Odaily/X. dune.com/queries/8275390: 133,963 trades, $3.886B/30d, last 2026-08-09 22:03 — by far the most active wallet in this list."),
    ("0xad6eaa735d9df3d7696fd03984379dae02ed8862", "Cumberland (DRW)",           "MM",  "Etherscan label 'Cumberland', ~$28M/8 chains, 84,145 txns. Lookonchain (X) reported early-Aug-2026 Hayes deposit into Cumberland/Galaxy. 0 dex.trades-taker matches 30d — OTC desk, expect maker/off-DEX flow, not spot-taker."),
    ("0x4adf44961a7cbe8ee46b2bf04e7198e879677825", "Galaxy Digital",             "MM",  "Etherscan label 'Galaxy Digital', ~$15.97M/3 chains, 1,304 txns. Named counterparty in the $125M SharpLink x Galaxy 'Onchain Yield Fund' announced 2026-08-07 (2d before this list). 0 dex.trades-taker matches 30d — OTC desk."),
    ("0xddacad3b1edee8e2f5b2e84f658202534fcb0374", "DWF Labs",                   "MM",  "Same address as v1's 'DWF Labs Hot'; Etherscan label, ~$2.45M/6 chains, 242 txns; partner Andrei Grachev has publicly disclosed this wallet family (PANews). 0 dex.trades-taker matches 30d — no Aug-2026-specific txn pinned, kept for continuity + MM diversity."),
    ("0xcc261ab4be137eacf57c19ed97c186b4d88004ca", "Jump Crypto",                "MM",  "Arkham label 'Jump Crypto', tracked on BscScan; Arkham-sourced reporting on Jump's Extended investment dated 2026-06-29 (41d before this list). 0 dex.trades-taker matches 30d — MM/venture arm, expect maker/OTC flow."),

    # ── VC: fund treasuries ────────────────────────────────────────────
    ("0x05e793ce0c6027323ac150f6d45c2344d28b6019", "a16z",                       "VC",  "Carried over from v1 (sole VC survivor of the 2026-07-02 prune). 0 dex.trades-taker matches 30d — VC treasury, not a DEX-taker wallet by design; only VC candidate in this pool."),

    # ── INS: founders / insiders ───────────────────────────────────────
    ("0x3ddfa8ec3052539b6c9549f12cea2c295cff5296", "Justin Sun",                 "INS", "Etherscan label 'Justin Sun'. TRM Labs/Coingape/Coinlive track large ETH tranches from Sun-linked wallets in 2026. CAVEAT: no dated Aug-2026 event pinned to this exact address (self-flagged weak recency at sourcing time) and 0 dex.trades-taker matches 30d. Kept as the only INS candidate; re-check next refresh."),

    # ── TRE: protocol/treasury wallets ─────────────────────────────────
    ("0xcd9e09b30d481cc33937ce33feb3d94d434f5f75", "SharpLink Gaming (ETH tre)", "TRE", "Arkham (X: @arkham): address that has sent SharpLink >$800M ETH to date, incl. a $40M ETH swap w/ Galaxy Digital. SharpLink x Galaxy jointly announced a $125M 'Onchain Yield Fund' 2026-08-07 (2d before this list) — very current. 0 dex.trades-taker matches 30d — treasury moves via OTC swap, not public spot-DEX taker flow."),
]


# ── EXCLUDED from v2 (kept here for the next refresh, do not re-research) ──
# TRD, verified active but cut only for the 25-cap / category-diversity
# budget (all cleanly passed >=2 trades on query 8275390 — good backups):
#   0xf50ca7624025aba9f2e92a70ce3496cca008bab8  85 trades, $16.3M/30d, last 2026-08-03
#   0x5d2d49c7371eac3a89ca780ffe4ff32d311d9761  45 trades, $12.4M/30d, last 2026-08-06
#   0xe043ffc0d9e0657431b052669ad5d6fd70cf983c  64 trades, $12.2M/30d, last 2026-08-08
#   0xa5dc676f2226ab03e65e9fb66e94b1d4b04e43cf  67 trades, $12.1M/30d, last 2026-08-08
# TRD, failed the explicit >=2-trade gate (measured, fell short — unlike the
# entity wallets above this metric squarely applies to it):
#   0x020ca66c30bec2c4fe3861a94e4db4a498a35872  Machi Big Brother — 1 trade,
#     $17.4k/30d. Also in v1 already; leave in v1, don't promote to v2 yet.
# TRD named-individual, dropped for compounding weak evidence (0 taker
# matches AND self-flagged recency outside the 60d window at sourcing time):
#   0x8def9f50456c6c4e37fa5d3d57f108ed23992dae  loracle.hl / Loracle
# MM, superseded by fresher/more-active addresses for the same entity:
#   0x0000006daea1723962647b7e189d311d757fb793  "Wintermute 1" (v1) — 0 taker
#     matches 30d; superseded by 0x51c72848...02a7f above (133,963 trades).
#   0xf584f8728b874a6a5c7a8d4d387c9aae9172d621  "Jump Trading" (v1) — 0 taker
#     matches 30d; superseded by Jump Crypto above for MM-slot budget.
# MM, cut for the 20%-of-list MM budget (weakest/most generic evidence of
# the MM candidate set — balance snapshot only, no named dated event):
#   0xe11970f2f3de9d637fb786f2d869f8fea44195ac  Amber Group


# Counterparty labels — recurring addresses across the curated feed.
# Populated as we see the same address hit multiple times in the tape.
# Verified from Etherscan public "Name Tag" fields.
# Copied unchanged from curated_wallets.py (v1) per the v2 build instructions.
COUNTERPARTIES = {
    # Jump Trading's CEX / OTC counterparties (verified 2026-07-02)
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io Deposit",
    "0xd6b38bea1b891c9064b1167fb68f8478f6829326": "Gate.io Deposit",
    "0xc7d3c88077d07b9ede6acd2060e5dacbcf08e668": "Bitget Deposit",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase 10",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance 17",
    "0x4a4aaa0155237881fbd5c34bfae16e985a7b068d": "OKX 154",
    "0x6643887784cb89cc9537dbb7ea20b00c45abeefb": "ByBit Deposit",
    "0xbcd986563ba93af5c6c92b16ab06e843a281a4a9": "(OTC — routing Binance↔OKX)",
    # Discovered via Jump OTC counterparty's flow — verified Etherscan Name Tag
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance 14",
    "0x3b98b9e9cb75c1cb1729aab8f09ced2936505e4f": "OKX 217",
    "0xf584f8728b874a6a5c7a8d4d387c9aae9172d621": "Jump Trading",
}


def counterparty_label(address: str) -> str:
    return COUNTERPARTIES.get(address.lower(), address[:12] + "…")


def addresses() -> list[str]:
    """Just the wallet addresses (for query splicing)."""
    return [w[0] for w in WHALES]


def label_for(address: str) -> str:
    """Human label for a given address; '(unlabeled)' if not in list."""
    address = address.lower()
    for a, name, _cat, _src in WHALES:
        if a.lower() == address:
            return name
    return "(unlabeled)"


def category_for(address: str) -> str:
    address = address.lower()
    for a, _name, cat, _src in WHALES:
        if a.lower() == address:
            return cat
    return "?"


if __name__ == "__main__":
    print(f"{len(WHALES)} curated wallets (v2, 2026-08-09):")
    for a, name, cat, src in WHALES:
        print(f"  [{cat}] {a}  {name}")
