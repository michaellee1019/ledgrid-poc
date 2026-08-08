"""Shared pixel-art glyph catalog used by emoji renderers."""

from typing import Dict, List

EMOJI_PATTERNS: Dict[str, List[str]] = {
    'smile': [
        ".....FFF.....",
        "...HFFFFFF...",
        "..FFFFFFFFF..",
        ".FFF.E.E.FFF.",
        ".FFF.....FFF.",
        "..FFFMMMFFF..",
        "...FFFFFFF..."
    ],
    'heart': [
        "...HH...HH...",
        "..HHHH.HHHH..",
        ".HHHHHHHHHHH.",
        ".HHHHHHHHHHH.",
        "..HHHHHHHHH..",
        "...HHHHHHH...",
        "....HHHHH...."
    ],
    # New emojis
    '🍆': [  # Eggplant
        "....HHH.....",
        "...HHHH.....",
        "..MMMMM.....",
        ".MMMMMMM....",
        ".MMMMMMM....",
        ".MMMMMMM....",
        "..MMMMM....."
    ],
    '🔥': [  # Fire (8 pixels wide)
        "...H....",
        "..HHH...",
        ".HHHHH..",
        "HHHHHHHH",
        "HHHHHHHH",
        ".HHHHHH.",
        "..HHHH.."
    ],
    '🤖': [  # Robot
        ".HHHHHHHHH..",
        ".H.E...E.H..",
        ".H.......H..",
        ".H..MMM..H..",
        ".H.......H..",
        ".HHHHHHHHH..",
        "............"
    ],
    '👀': [  # Eyes
        ".EEE...EEE..",
        "EEEEE.EEEEE.",
        "EE.EEEEE.EE.",
        "EEEEE.EEEEE.",
        ".EEE...EEE..",
        "............",
        "............"
    ],
    '👻': [  # Ghost
        "..FFFFFFF...",
        ".FFFFFFFFF..",
        ".FF.E.E.FF..",
        ".FFFFFFFFF..",
        ".FF..M..FF..",
        ".FFFFFFFFF..",
        ".F.F.F.F.F.."
    ],
    '🌈': [  # Rainbow
        "............",
        ".HHHHHHHHH..",
        "HHHHHHHHHHHH",
        "HHHHHHHHHHHH",
        "............",
        "............",
        "............"
    ],
    # Letters A-Z (8 pixels wide, 4 pixels tall to fit multiple lines)
    'A': [
        "..FFFF..",
        ".FF..FF.",
        ".FFFFFF.",
        ".FF..FF."
    ],
    'B': [
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FFFFFF....."
    ],
    'C': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF.........",
        ".FF.........",
        ".FF.........",
        ".FF...FF....",
        "..FFFFF....."
    ],
    'D': [
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FFFFFF....."
    ],
    'E': [
        ".FFFFFFF....",
        ".FF.........",
        ".FF.........",
        ".FFFFF......",
        ".FF.........",
        ".FF.........",
        ".FFFFFFF...."
    ],
    'F': [
        ".FFFFFFF....",
        ".FF.........",
        ".FF.........",
        ".FFFFF......",
        ".FF.........",
        ".FF.........",
        ".FF........."
    ],
    'G': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF.........",
        ".FF..FFF....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    'H': [
        ".FF..FF.",
        ".FF..FF.",
        ".FF..FF.",
        ".FFFFFF.",
        ".FF..FF.",
        ".FF..FF.",
        ".FF..FF."
    ],
    'I': [
        ".FFFFFF.",
        "..FFF...",
        "..FFF...",
        "..FFF...",
        "..FFF...",
        "..FFF...",
        ".FFFFFF."
    ],
    'J': [
        ".FFFFFFF....",
        "....FF......",
        "....FF......",
        "....FF......",
        "....FF......",
        ".FF.FF......",
        "..FFF......."
    ],
    'K': [
        ".FF...FF....",
        ".FF..FF.....",
        ".FF.FF......",
        ".FFFF.......",
        ".FF.FF......",
        ".FF..FF.....",
        ".FF...FF...."
    ],
    'L': [
        ".FF.........",
        ".FF.........",
        ".FF.........",
        ".FF.........",
        ".FF.........",
        ".FF.........",
        ".FFFFFFF...."
    ],
    'M': [
        ".FF...FF....",
        ".FFF.FFF....",
        ".FFFFFFF....",
        ".FF.F.FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF...."
    ],
    'N': [
        ".FF...FF....",
        ".FFF..FF....",
        ".FFFF.FF....",
        ".FF.FFFF....",
        ".FF..FFF....",
        ".FF...FF....",
        ".FF...FF...."
    ],
    'O': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    'P': [
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FFFFFF.....",
        ".FF.........",
        ".FF.........",
        ".FF........."
    ],
    'Q': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF.F.FF....",
        ".FF..FF.....",
        "..FFFFF.F..."
    ],
    'R': [
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        ".FFFFFF.....",
        ".FF.FF......",
        ".FF..FF.....",
        ".FF...FF...."
    ],
    'S': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF.........",
        "..FFFFF.....",
        "......FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    'T': [
        ".FFFFFFF....",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "...FFF......"
    ],
    'U': [
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    'V': [
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        "..FF.FF.....",
        "...FFF......"
    ],
    'W': [
        ".FF...FF....",
        ".FF...FF....",
        ".FF...FF....",
        ".FF.F.FF....",
        ".FFFFFFF....",
        ".FFF.FFF....",
        ".FF...FF...."
    ],
    'X': [
        ".FF...FF....",
        "..FF.FF.....",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "..FF.FF.....",
        ".FF...FF...."
    ],
    'Y': [
        ".FF...FF....",
        "..FF.FF.....",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "...FFF......",
        "...FFF......"
    ],
    'Z': [
        ".FFFFFFF....",
        "......FF....",
        ".....FF.....",
        "....FF......",
        "...FF.......",
        "..FF........",
        ".FFFFFFF...."
    ],
    # Numbers 0-9
    '0': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF..FFF....",
        ".FF.F.FF....",
        ".FFF..FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    '1': [
        "...FF.......",
        "..FFF.......",
        "...FF.......",
        "...FF.......",
        "...FF.......",
        "...FF.......",
        ".FFFFF......"
    ],
    '2': [
        "..FFFFF.....",
        ".FF...FF....",
        "......FF....",
        "....FFF.....",
        "..FFF.......",
        ".FF.........",
        ".FFFFFFF...."
    ],
    '3': [
        "..FFFFF.....",
        ".FF...FF....",
        "......FF....",
        "...FFFF.....",
        "......FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    '4': [
        "....FFF.....",
        "...FFFF.....",
        "..FF.FF.....",
        ".FF..FF.....",
        ".FFFFFFF....",
        ".....FF.....",
        ".....FF....."
    ],
    '5': [
        ".FFFFFFF....",
        ".FF.........",
        ".FFFFFF.....",
        "......FF....",
        "......FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    '6': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF.........",
        ".FFFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    '7': [
        ".FFFFFFF....",
        "......FF....",
        ".....FF.....",
        "....FF......",
        "...FF.......",
        "..FF........",
        "..FF........"
    ],
    '8': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFF....."
    ],
    '9': [
        "..FFFFF.....",
        ".FF...FF....",
        ".FF...FF....",
        "..FFFFFF....",
        "......FF....",
        ".FF...FF....",
        "..FFFFF....."
    ]
}

__all__ = ["EMOJI_PATTERNS"]
