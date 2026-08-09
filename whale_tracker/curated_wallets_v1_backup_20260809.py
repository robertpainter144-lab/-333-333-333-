"""
Hand-curated list of publicly-known crypto whale / cabal / insider wallets.

Every entry sourced from Etherscan's public labels or public reporting.
Prefer known market-makers and VC treasuries over "big holders" — MMs
route deal flow, VCs signal thesis, and insider wallets flag OTC edges.

Add more from:
  - https://arkm.com/explorer/entity/<name>   (Arkham entity pages)
  - https://www.lookonchain.com/               (Twitter/X @lookonchain)
  - Nansen Smart Money exports (paid but authoritative)

Categories used downstream for grouping in the feed.
"""

# (address, label, category, source_url)
WHALES = [
    # ── Market makers ──────────────────────────────────────────────
    ("0x0000006daea1723962647b7e189d311d757fb793", "Wintermute 1",         "MM",    "etherscan.io/address/0x0000006daea1723962647b7e189d311d757fb793"),
    # PRUNED 2026-07-02: Wintermute 2/main/Negotiator/Multisig, Jump 2 all dormant 46-895d
    ("0xf584f8728b874a6a5c7a8d4d387c9aae9172d621", "Jump Trading",         "MM",    "etherscan.io/address/0xf584f8728b874a6a5c7a8d4d387c9aae9172d621"),

    # ── VC treasuries ──────────────────────────────────────────────
    ("0x05e793ce0c6027323ac150f6d45c2344d28b6019", "a16z",                 "VC",    "etherscan.io/address/0x05e793ce0c6027323ac150f6d45c2344d28b6019"),
    # PRUNED 2026-07-02: Paradigm (3058d idle), Polychain (763d idle) — these labeled
    # addresses aren't their live trading wallets. Need to hunt current ones.

    # ── Founders / insiders ────────────────────────────────────────
    ("0x3ddfa8ec3052539b6c9549f12cea2c295cff5296", "Justin Sun",           "INS",   "etherscan.io/address/0x3ddfa8ec3052539b6c9549f12cea2c295cff5296"),
    ("0xd8da6bf26964af9d7eed9e03e53415d37aa96045", "Vitalik (vitalik.eth)","INS",   "etherscan.io/address/0xd8da6bf26964af9d7eed9e03e53415d37aa96045"),
    ("0xab5801a7d398351b8be11c439e05c5b3259aec9b", "Vitalik VB",           "INS",   "etherscan.io/address/0xab5801a7d398351b8be11c439e05c5b3259aec9b"),

    # ── Added 2026-07-02 via research pass ─────────────────────────
    # DWF Labs — market maker, rotates wallets, these are current-ish
    ("0xddacad3b1edee8e2f5b2e84f658202534fcb0374", "DWF Labs Hot",         "MM",    "etherscan.io/address/0xddacad3b1edee8e2f5b2e84f658202534fcb0374"),
    ("0xd4b69e8d62c880e9dd55d419d5e07435c3538342", "DWF Labs Secondary",   "MM",    "etherscan.io/address/0xd4b69e8d62c880e9dd55d419d5e07435c3538342"),
    # PRUNED: DWF Labs Token Buys (57d idle), 0xB041 Panic Seller (97d), Multicoin (130d)
    # Public traders outed by Lookonchain / Arkham
    ("0x020ca66c30bec2c4fe3861a94e4db4a498a35872", "Machi Big Brother",    "TRD",   "etherscan.io/address/0x020ca66c30bec2c4fe3861a94e4db4a498a35872"),
    # OTC / unlabeled counterparty of Jump — routes BASED and LIT
    ("0xbcd986563ba93af5c6c92b16ab06e843a281a4a9", "Jump OTC counterparty","MM?",   "seen in etherscan_feed.py output"),

    # ── ADD MORE HERE ──────────────────────────────────────────────
    # From Lookonchain highlights, Arkham entity pages, Nansen exports.
    # Preferred format: (address, label, category, source)
    # Categories: MM (market maker) | VC (fund) | INS (insider/founder)
    #             | TRD (public trader) | TRE (protocol treasury)
]


# Counterparty labels — recurring addresses across the curated feed.
# Populated as we see the same address hit multiple times in the tape.
# Verified from Etherscan public "Name Tag" fields.
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
    print(f"{len(WHALES)} curated wallets:")
    for a, name, cat, src in WHALES:
        print(f"  [{cat}] {a}  {name}")
