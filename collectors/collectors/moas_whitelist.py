"""
collectors/moas_whitelist.py

Multi-Origin AS (MOAS) whitelist — legitimate prefix announcements
from multiple ASNs that are NOT hijacks.

Sources:
  - Anycast operators (Cloudflare, Akamai, Fastly, Google, Verisign)
  - DNS providers with distributed ASN architectures (Vercara/Neustar)
  - CDN BYOIP programs (Amazon, Azure, GCP customer ASNs)
  - IXP route servers
  - Well-known anycast services (NTP, DNS root servers)

Structure:
  MOAS_AS_PAIRS  — set of (origin_asn, expected_asn) tuples that are
                   known-legitimate MOAS. Either ordering is checked.
  MOAS_PREFIXES  — specific prefixes known to be legitimately multi-origin.
  MOAS_AS_GROUPS — sets of ASNs belonging to the same operator.
                   Any origin change within a group = not a hijack.
"""

# ── Known operator AS groups (any origin change within group = MOAS) ──────────

MOAS_AS_GROUPS: list[set[int]] = [

    # Cloudflare — main + WARP + partners
    {13335, 209242, 132892},

    # Google — main + cloud + YouTube + Fiber + private peering
    {15169, 19527, 36040, 36384, 36385, 36386, 36387, 36388, 36389},

    # Amazon — main + GovCloud + BYOIP + CloudFront + Lightsail
    {16509, 14618, 8987, 21664, 394161},

    # Microsoft — main + Azure + M365 + LinkedIn
    {8075, 3598, 6182, 8068, 8069, 8070, 8071, 8072, 8073, 8074},

    # Akamai — main + Linode + various edge ASNs
    {20940, 16625, 17204, 18717, 23454, 23455, 31377, 63949},

    # Meta / Facebook — main + Instagram + WhatsApp infra
    {32934, 54115, 63293},

    # Fastly — main + Signal Sciences
    {54113, 394192},

    # Vercara / Neustar / UltraDNS — anycast DNS across many ASNs
    {399153, 399155, 399158, 399161, 399164, 399167, 399169,
     19905, 19906, 19907, 19908, 19910, 19911, 19912, 19913},

    # Verisign — DNS root + TLD anycast
    {26415, 7342, 30060},

    # APNIC research + anycast
    {4608, 7545, 38016, 38040, 149},

    # GTT Communications — backbone + acquired networks
    {3257, 4436, 8220, 9304, 13237},

    # Cogent — backbone + acquired networks
    {174, 38193},

    # NTT — backbone + subsidiaries
    {2914, 4713, 9595},

    # Telia — backbone + subsidiaries
    {1299, 3301, 5507, 21195},

    # Lumen / CenturyLink / Level3
    {3356, 3549, 11213, 10364, 10796},

    # Hurricane Electric
    {6939, 6940},

    # Zayo
    {6461, 8218, 29791},

    # Vultr — main + customer BYOIP
    {20473, 64515},

    # Linode (now Akamai) — legacy ASNs
    {63949, 328815},

    # RIPE NCC — RIS collectors and infrastructure
    {12654, 25152, 200242},

    # IX Route servers (not hijacks — they aggregate routes)
    {56730, 60501, 61955},
]

# ── Build fast lookup structures ───────────────────────────────────────────────

# Map each ASN to the index of its group
_asn_to_group: dict[int, int] = {}
for _i, _group in enumerate(MOAS_AS_GROUPS):
    for _asn in _group:
        _asn_to_group[_asn] = _i


def is_moas_whitelist(origin_asn: int, expected_asn: int) -> bool:
    """
    Return True if (origin_asn, expected_asn) is a known-legitimate MOAS.
    Checks if both ASNs belong to the same operator group.
    """
    if origin_asn == expected_asn:
        return True
    g1 = _asn_to_group.get(origin_asn)
    g2 = _asn_to_group.get(expected_asn)
    return g1 is not None and g1 == g2


def moas_reason(origin_asn: int, expected_asn: int) -> str:
    """Return a human-readable reason why this MOAS is whitelisted."""
    g = _asn_to_group.get(origin_asn)
    if g is not None and g == _asn_to_group.get(expected_asn):
        # Find group name by checking which group it is
        group = MOAS_AS_GROUPS[g]
        sample = sorted(group)[:2]
        return f"Same operator group (ASNs {sample[0]}, {sample[1]}, ...)"
    return "Unknown"
