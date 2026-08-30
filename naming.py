"""Deterministic friendly-name generator.

Every peer gets a stable, human-readable name of the form {modifier}{trick},
derived from hashing its peer id. Same peer id -> same name on every machine,
no state. (Convention shared with the CpyPst project's get_friendly_name.)
"""
import hashlib

_SKATE_TRICKS = [
    # Street flips & shuvits
    "Kickflip", "Heelflip", "360Flip", "Hardflip", "VarialKickflip",
    "PopShuvit", "Bigspin", "ShoveIt", "DoubleKickflip", "DoubleHeelflip",
    "DoubleShuvit", "TreFlip", "DoubleTreFlip", "Frontside180Shuvit",
    "Backside180Shuvit", "PopFrontsideShuvit", "PopBacksideShuvit",
    # Street slides & grinds
    "Boardslide", "Lipslide", "SmithGrind", "Nosegrind", "Bluntslide",
    "CrookedGrind", "FeebleGrind", "IcePickGrind", "Fifty50Grind", "FiveZeroGrind",
    "Tailslide", "Noseslide", "PidginGrind", "RockToFake", "Manual",
    "Nosemanual", "Nosepress", "Tailpress", "BluntToFeeble",
    "Frontside505", "Backside505", "SmithSlide", "CrookedSlide",
    # Vert & park
    "McTwist", "AirToFakie", "Stalefish", "MelonGrab", "MethodAir",
    "IndyGrab", "MuteGrab", "Tantrum", "Rodeo", "CabinAir", "Invert",
    "Tailgrab", "Handplant", "RockSolid", "BottleFlip", "Hangten",
    "Caballerial", "AirToReverse", "BluntAir", "StarPlant",
    # Technical & switch/fakie variations
    "PhantomFlip", "SwitchKickflip", "SwitchHeelflip", "NollieKickflip",
    "FakieFlip", "BlindFlip", "Impossible", "DoubleImpossible", "ShoveIt180",
    "PopFrontside180", "PopBackside180", "TreFlipHardflip", "KickflipBigspin",
    "HeelflipBigspin", "VarialShuvit", "Caveman", "CatLeap", "Coneflip",
    "SwitchPhantom", "FakiePhantom", "NollieHeelflip", "BlindHeelflip",
    # Big air & combos
    "DoubleMcTwist", "QuadFlip", "TripleShuvit", "Kickflip540",
    "Heelflip360", "LaserHack", "MegaFlip", "SwitchBigspin",
    "FakieBigspin", "BlindBigspin", "SwitchHardflip", "NollieHeelflip",
    "DoubleTreFlip", "TripleKickflip", "QuadrupleShuvit",
    # Aerials & grabs
    "FrontsideAir", "BacksideAir", "InlineAir",
    "Rosalin", "Rosallind", "BakerAir", "Caballerial",
    # Classics & style
    "Ollie", "PopShoveIt", "Frontside180Air", "Backside180Air",
    "Frontside540", "Backside540", "Frontside720", "Backside720",
    "KickflipNosegrind", "HeelflipBluntslide", "VarialLipslide",
    "PopFrontside", "PopBackside", "FrontsidePopShuvit",
]

_TRICK_MODIFIERS = [
    # Flip variations
    "Flip", "DoubleFlip", "TreFlip", "Hardflip", "Phantom",
    "Varial", "Nosevarial", "Toevarial", "Kickflip", "Heelflip",
    # Spin counts & rotations
    "180", "360", "540", "720", "900", "DoubleSpin", "TripleSpin",
    "HalfCab", "FullCab", "QuarterFlip", "HalfFlip",
    # Grab names
    "Indy", "Mute", "Stalefish", "Melon", "Method", "Tailgrab",
    "Handplant", "Rodeo", "Cabin", "Slot", "Roastbeef", "Nosgrass",
    "Tantrum", "Air", "Grab", "Pinch", "Huck", "Lazer",
    # Slide/grind styles
    "Boardslide", "Lipslide", "Smith", "Blunt", "Feeble",
    "Crooked", "Nosegrind", "Tailslide", "Pidgin", "Rockslide",
    # Direction & stance
    "Frontside", "Backside", "Switch", "Fakie", "Blind", "Nollie",
    "Regular", "Goofy", "PopShuvit", "Bigspin", "ShoveIt",
    # Size / power adjectives
    "Mega", "Giga", "Ultra", "Hyper", "Super", "Micro", "Mini",
    "Max", "Turbo", "Mach", "Atomic", "Quantum", "Cosmic", "Solar",
    # Rare / creative modifiers
    "Coneflip", "Caveman", "CatLeap", "Barkley", "Ollie",
    "Impossible", "DoubleImp", "TreFlip", "KickflipBigspin",
    "HeelflipBigspin", "VarialShuvit", "SwitchHardflip",
    "QuadFlip", "TripleShuvit", "QuadrupleShuvit",
    "McTwist", "Caballerial", "StarPlant", "RockSolid",
]


def friendly_name(peer_id: str) -> str:
    """Deterministic {modifier}{trick} name from a peer id (e.g. 'MegaBoardslide')."""
    digest = hashlib.sha256(peer_id.encode()).digest()
    trick = _SKATE_TRICKS[digest[0] % len(_SKATE_TRICKS)]
    modifier = _TRICK_MODIFIERS[digest[1] % len(_TRICK_MODIFIERS)]
    return f"{modifier}{trick}"
