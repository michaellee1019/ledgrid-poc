#!/usr/bin/env python3
"""Generate original, native-resolution pixel-art GIFs for the LED wall.

The output is deliberately authored at 32x138.  That avoids interpolation in
the GIF plugin and keeps every square pixel crisp on the physical installation.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw


WIDTH = 32
HEIGHT = 138
FRAME_COUNT = 8
DURATION_MS = 140
CREATED_AT = "2026-07-21T00:00:00Z"


@dataclass(frozen=True)
class Scene:
    slug: str
    name: str
    motif: str
    environment: str
    background: tuple[int, int, int]
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]
    category: str
    description: str
    tags: tuple[str, ...]


SCENES = (
    Scene("frog-pond-ripple", "Frog Pond Ripple", "frog", "water", (1, 10, 17), (76, 224, 92), (174, 255, 112), "Nature", "Tiny frogs bob between rippling lily pads.", ("frog", "water", "cute")),
    Scene("cozy-window-cats", "Cozy Window Cats", "cat", "cozy", (11, 6, 18), (255, 167, 91), (255, 232, 174), "Cozy", "Sleepy cats blink beside warm midnight windows.", ("cat", "cozy", "warm")),
    Scene("axolotl-bubble-column", "Axolotl Bubble Column", "axolotl", "bubbles", (0, 9, 23), (255, 139, 178), (102, 227, 255), "Aquatic", "Pink axolotls paddle through a rising bubble column.", ("axolotl", "bubbles", "pink")),
    Scene("shy-ghost-parade", "Shy Ghost Parade", "ghost", "stars", (8, 5, 20), (220, 218, 255), (133, 105, 255), "Spooky Cute", "Friendly ghosts wave through a violet night.", ("ghost", "night", "cute")),
    Scene("jellyfish-lanterns", "Jellyfish Lanterns", "jellyfish", "bubbles", (1, 5, 25), (117, 218, 255), (211, 121, 255), "Aquatic", "Glowing jellyfish pulse and trail tiny bubbles.", ("jellyfish", "glow", "ocean")),
    Scene("mushroom-village", "Mushroom Village", "mushroom", "forest", (4, 10, 12), (255, 78, 103), (255, 215, 121), "Cozy", "Little mushroom homes glow beneath forest stars.", ("mushroom", "forest", "cozy")),
    Scene("bumblebee-garden", "Bumblebee Garden", "bee", "garden", (7, 12, 20), (255, 197, 45), (245, 246, 216), "Nature", "Round bees buzz between pixel daisies.", ("bee", "flowers", "yellow")),
    Scene("moonlit-ducks", "Moonlit Ducks", "duck", "water", (2, 6, 24), (255, 240, 189), (255, 184, 56), "Cozy", "Tiny ducks paddle across moonlit reflections.", ("duck", "moon", "water")),
    Scene("koi-ribbon", "Koi Ribbon", "koi", "water", (0, 9, 18), (255, 104, 55), (245, 243, 222), "Aquatic", "Koi weave up a dark vertical stream.", ("koi", "water", "orange")),
    Scene("cotton-candy-clouds", "Cotton Candy Clouds", "cloud", "stars", (12, 8, 32), (255, 173, 216), (154, 210, 255), "Dreamy", "Pink clouds drift between warm little stars.", ("cloud", "pastel", "dreamy")),
    Scene("happy-star-fall", "Happy Star Fall", "star", "stars", (3, 6, 24), (255, 224, 75), (255, 148, 67), "Dreamy", "Cheerful stars tumble slowly down the wall.", ("stars", "yellow", "cute")),
    Scene("peach-orchard", "Peach Orchard", "peach", "garden", (11, 8, 21), (255, 109, 145), (79, 210, 101), "Nature", "Blushing peaches sway on leafy branches.", ("fruit", "pink", "garden")),
    Scene("tiny-robot-patrol", "Tiny Robot Patrol", "robot", "tech", (2, 7, 15), (137, 211, 255), (122, 255, 165), "Sci-Fi", "Pocket robots blink and patrol a neon tower.", ("robot", "neon", "cute")),
    Scene("snails-after-rain", "Snails After Rain", "snail", "rain", (2, 14, 16), (191, 112, 255), (123, 224, 96), "Nature", "Purple-shelled snails inch along rainy leaves.", ("snail", "rain", "forest")),
    Scene("firefly-bottle", "Firefly Bottle", "jar", "fireflies", (2, 10, 12), (255, 231, 89), (92, 220, 138), "Ambient", "Fireflies wink inside tiny glass jars.", ("fireflies", "jar", "glow")),
    Scene("moon-bunny-meadow", "Moon Bunny Meadow", "bunny", "stars", (5, 7, 24), (246, 232, 255), (186, 134, 255), "Dreamy", "White bunnies bounce beneath violet moons.", ("bunny", "moon", "cute")),
    Scene("lantern-tree", "Lantern Tree", "tree", "fireflies", (7, 5, 18), (190, 102, 255), (255, 190, 67), "Ambient", "Purple trees shelter softly swinging lanterns.", ("tree", "lantern", "purple")),
    Scene("hydrangea-rain", "Hydrangea Rain", "flower", "rain", (4, 8, 23), (103, 132, 255), (162, 105, 255), "Nature", "Blue hydrangeas shimmer in a gentle shower.", ("flowers", "rain", "blue")),
    Scene("jolly-slime-stack", "Jolly Slime Stack", "slime", "stars", (5, 9, 18), (116, 235, 126), (255, 130, 190), "Arcade", "Colorful slimes bounce in precarious stacks.", ("slime", "bounce", "arcade")),
    Scene("pocket-rocket", "Pocket Rocket", "rocket", "space", (2, 4, 18), (238, 239, 255), (255, 103, 72), "Sci-Fi", "Small rockets climb through lavender exhaust.", ("rocket", "space", "motion")),
    Scene("planet-parade", "Planet Parade", "planet", "space", (1, 4, 18), (255, 176, 70), (105, 145, 255), "Sci-Fi", "Ringed planets and blue moons orbit in a column.", ("planets", "space", "orbit")),
    Scene("penguin-ice-fishing", "Penguin Ice Fishing", "penguin", "snow", (4, 10, 27), (235, 242, 255), (107, 203, 255), "Winter", "Tiny penguins fish from floating ice shelves.", ("penguin", "winter", "cute")),
    Scene("coral-fish-friends", "Coral Fish Friends", "fish", "bubbles", (0, 8, 24), (255, 116, 60), (64, 218, 255), "Aquatic", "Striped fish dart through a miniature reef.", ("fish", "coral", "ocean")),
    Scene("cozy-shelf-naps", "Cozy Shelf Naps", "shelf", "cozy", (13, 7, 13), (255, 173, 72), (198, 110, 68), "Cozy", "Sleepy blobs nap on warm wooden shelves.", ("cozy", "shelf", "sleepy")),
    Scene("grape-bounce", "Grape Bounce", "grape", "garden", (9, 5, 18), (148, 80, 230), (100, 222, 104), "Food", "Glossy grape bunches bounce from leafy vines.", ("grapes", "fruit", "purple")),
    Scene("campfire-ghost-stories", "Campfire Ghost Stories", "campfire", "forest", (4, 5, 13), (245, 235, 255), (255, 111, 46), "Spooky Cute", "Little ghosts warm up beside flickering campfires.", ("ghost", "campfire", "cozy")),
    Scene("curtain-cat-watch", "Curtain Cat Watch", "curtain_cat", "cozy", (10, 4, 15), (255, 185, 91), (159, 65, 151), "Cozy", "Black cats watch lanterns through purple curtains.", ("cat", "window", "cozy")),
    Scene("sunflower-hamsters", "Sunflower Hamsters", "hamster", "garden", (4, 11, 13), (255, 211, 42), (215, 138, 74), "Nature", "Round hamsters peek from towering sunflowers.", ("hamster", "sunflower", "yellow")),
    Scene("balloon-blobs", "Balloon Blobs", "balloon", "stars", (7, 7, 25), (255, 125, 181), (255, 225, 94), "Party", "Happy blobs float under bobbing balloons.", ("balloon", "party", "pink")),
    Scene("cactus-bloom-dance", "Cactus Bloom Dance", "cactus", "stars", (4, 9, 16), (82, 222, 113), (255, 105, 177), "Nature", "Flower-crowned cacti wiggle beneath desert stars.", ("cactus", "flower", "cute")),
    Scene("sleepy-bat-cave", "Sleepy Bat Cave", "bat", "stars", (5, 3, 15), (154, 107, 224), (246, 205, 255), "Spooky Cute", "Small bats flap, yawn, and settle under stars.", ("bat", "night", "purple")),
    Scene("cupcake-sprinkle-party", "Cupcake Sprinkle Party", "cupcake", "confetti", (15, 6, 21), (255, 121, 188), (125, 219, 255), "Party", "Frosted cupcakes bounce through bright sprinkles.", ("cupcake", "party", "sweet")),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("assets/gifs"))
    parser.add_argument("--preset-dir", type=Path, default=Path("presets/animations/gif_animation"))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def rect(draw: ImageDraw.ImageDraw, box, fill):
    draw.rectangle(tuple(int(v) for v in box), fill=fill)


def oval(draw: ImageDraw.ImageDraw, box, fill):
    draw.ellipse(tuple(int(v) for v in box), fill=fill)


def eye(draw: ImageDraw.ImageDraw, x: int, y: int, blink: bool = False):
    if blink:
        rect(draw, (x, y + 1, x + 1, y + 1), (18, 12, 28))
    else:
        rect(draw, (x, y, x + 1, y + 1), (18, 12, 28))
        rect(draw, (x, y, x, y), (245, 248, 255))


def sparkle(draw: ImageDraw.ImageDraw, x: int, y: int, color):
    rect(draw, (x, y - 1, x, y + 1), color)
    rect(draw, (x - 1, y, x + 1, y), color)


def background(draw: ImageDraw.ImageDraw, scene: Scene, frame: int):
    rng = random.Random(f"{scene.slug}:{frame // 2}")
    env = scene.environment
    if env in {"stars", "space", "snow"}:
        for i in range(22):
            x = rng.randrange(WIDTH)
            y = (rng.randrange(HEIGHT) + frame * (1 if env == "snow" else 0)) % HEIGHT
            color = scene.secondary if i % 5 == frame % 5 else (86, 95, 145)
            if i % 7 == 0:
                sparkle(draw, x, y, color)
            else:
                rect(draw, (x, y, x, y), color)
    elif env in {"bubbles", "water"}:
        for i in range(18):
            x = (i * 11 + (i % 3) * 5) % WIDTH
            y = (i * 19 - frame * (1 + i % 2)) % HEIGHT
            color = (32, 111, 152) if i % 3 else scene.secondary
            oval(draw, (x, y, x + (i % 2) + 1, y + (i % 2) + 1), color)
        if env == "water":
            for y in range(12, HEIGHT, 28):
                rect(draw, (2, y, 11, y), (18, 69, 83))
                rect(draw, (17, y + 3, 29, y + 3), (18, 69, 83))
    elif env == "rain":
        for i in range(25):
            x = (i * 7 + 3) % WIDTH
            y = (i * 17 + frame * 3) % HEIGHT
            rect(draw, (x, y, x, min(HEIGHT - 1, y + 2)), (50, 94, 138))
    elif env in {"garden", "forest", "fireflies"}:
        for y in range(4, HEIGHT, 18):
            x = (y * 5 + frame // 2) % WIDTH
            color = scene.secondary if env == "fireflies" else (36, 93, 60)
            if env == "fireflies":
                sparkle(draw, x, y, color)
            else:
                rect(draw, (x, y, x + 2, y + 1), color)
                rect(draw, (x + 1, y - 1, x + 1, y + 2), color)
    elif env == "cozy":
        for y in range(8, HEIGHT, 31):
            rect(draw, (2, y, 29, y + 19), (22, 13, 29))
            rect(draw, (6, y + 3, 25, y + 15), (73, 39, 46))
            rect(draw, (8, y + 5, 23, y + 13), (245, 151, 66))
            rect(draw, (9, y + 6, 22, y + 12), (255, 211, 112))
            rect(draw, (15, y + 5, 16, y + 13), (61, 36, 45))
    elif env == "tech":
        for y in range(5, HEIGHT, 14):
            rect(draw, (3, y, 7, y), (20, 80, 102))
            rect(draw, (24, y + 5, 29, y + 5), (25, 103, 87))
    elif env == "confetti":
        colors = (scene.primary, scene.secondary, (255, 226, 90), (107, 242, 150))
        for i in range(30):
            x = (i * 13 + frame) % WIDTH
            y = (i * 23 + frame * (i % 3)) % HEIGHT
            rect(draw, (x, y, x + (i % 2), y + 1), colors[i % len(colors)])


def draw_motif(draw: ImageDraw.ImageDraw, motif: str, x: int, y: int, frame: int, a, b):
    blink = frame in (3, 4)
    flap = frame % 4 in (1, 2)
    if motif == "frog":
        oval(draw, (x - 7, y + 8, x + 7, y + 10), (31, 93, 55)); oval(draw, (x - 5, y, x + 5, y + 8), a)
        oval(draw, (x - 5, y - 3, x - 1, y + 2), a); oval(draw, (x + 1, y - 3, x + 5, y + 2), a)
        eye(draw, x - 4, y - 1, blink); eye(draw, x + 3, y - 1, blink); rect(draw, (x - 1, y + 5, x + 1, y + 5), b)
    elif motif == "cat":
        oval(draw, (x - 6, y - 3, x + 6, y + 8), a); rect(draw, (x - 6, y - 6, x - 2, y), a); rect(draw, (x + 2, y - 6, x + 6, y), a)
        eye(draw, x - 3, y + 1, blink); eye(draw, x + 2, y + 1, blink); rect(draw, (x, y + 4, x, y + 4), (255, 126, 149))
        rect(draw, (x - 4, y + 9, x + 4, y + 13), a); rect(draw, (x + 5, y + 10, x + 7, y + 11 + (frame % 2)), a)
    elif motif == "axolotl":
        oval(draw, (x - 6, y - 4, x + 6, y + 5), a)
        for dy in (-3, 0, 3): rect(draw, (x - 9, y + dy - 1, x - 6, y + dy), b); rect(draw, (x + 6, y + dy - 1, x + 9, y + dy), b)
        eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink); rect(draw, (x - 1, y + 3, x + 1, y + 3), (126, 52, 102))
    elif motif == "ghost":
        oval(draw, (x - 6, y - 6, x + 6, y + 6), a); rect(draw, (x - 6, y, x + 6, y + 8), a)
        for dx in (-5, 0, 5): oval(draw, (x + dx - 2, y + 5, x + dx + 2, y + 10), a)
        eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink); rect(draw, (x - 1, y + 4, x + 1, y + 5), b)
    elif motif == "jellyfish":
        oval(draw, (x - 7, y - 6, x + 7, y + 5), a); rect(draw, (x - 7, y, x + 7, y + 4), a)
        eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink)
        for dx in (-5, -1, 3, 6): rect(draw, (x + dx, y + 5, x + dx, y + 10 + ((frame + dx) % 3)), b)
    elif motif == "mushroom":
        oval(draw, (x - 8, y - 7, x + 8, y + 4), a); rect(draw, (x - 6, y, x + 6, y + 4), a)
        rect(draw, (x - 4, y + 4, x + 4, y + 12), b); eye(draw, x - 3, y + 7, blink); eye(draw, x + 2, y + 7, blink)
        rect(draw, (x - 4, y - 2, x - 2, y), b); rect(draw, (x + 3, y - 5, x + 5, y - 3), b)
    elif motif == "bee":
        oval(draw, (x - 6, y - 3, x + 6, y + 5), a); rect(draw, (x - 2, y - 3, x, y + 5), (34, 26, 30)); rect(draw, (x + 3, y - 2, x + 5, y + 4), (34, 26, 30))
        oval(draw, (x - 4, y - 7 - int(flap), x, y - 2), b); oval(draw, (x + 1, y - 7 - int(flap), x + 5, y - 2), b); eye(draw, x - 5, y, blink)
    elif motif == "duck":
        oval(draw, (x - 7, y, x + 7, y + 8), a); oval(draw, (x - 5, y - 6, x + 3, y + 3), a); rect(draw, (x + 3, y - 2, x + 8, y + 1), b)
        eye(draw, x, y - 3, blink); rect(draw, (x - 9, y + 9, x + 9, y + 9), (35, 95, 132))
    elif motif == "koi":
        oval(draw, (x - 7, y - 4, x + 6, y + 4), b); draw.polygon(((x + 5, y), (x + 10, y - 5), (x + 10, y + 5)), fill=a)
        rect(draw, (x - 4, y - 4, x, y + 4), a); eye(draw, x - 6, y - 1, blink)
    elif motif == "cloud":
        oval(draw, (x - 8, y - 2, x + 8, y + 6), a); oval(draw, (x - 5, y - 6, x + 1, y + 3), a); oval(draw, (x, y - 8, x + 6, y + 4), a)
        eye(draw, x - 3, y + 1, blink); eye(draw, x + 3, y + 1, blink); rect(draw, (x - 1, y + 4, x + 1, y + 4), b)
    elif motif == "star":
        points = ((x, y - 8), (x + 2, y - 2), (x + 8, y - 2), (x + 3, y + 2), (x + 5, y + 8), (x, y + 4), (x - 5, y + 8), (x - 3, y + 2), (x - 8, y - 2), (x - 2, y - 2))
        draw.polygon(points, fill=a); eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink)
    elif motif == "peach":
        oval(draw, (x - 7, y - 4, x + 7, y + 9), a); rect(draw, (x, y - 8, x + 1, y - 3), (74, 126, 62)); oval(draw, (x + 1, y - 9, x + 7, y - 4), b)
        eye(draw, x - 3, y + 2, blink); eye(draw, x + 2, y + 2, blink)
    elif motif == "robot":
        rect(draw, (x - 6, y - 6, x + 6, y + 5), a); rect(draw, (x - 4, y - 4, x + 4, y + 2), (13, 37, 49)); rect(draw, (x, y - 10, x, y - 6), b); rect(draw, (x - 3, y + 6, x + 3, y + 11), b)
        rect(draw, (x - 3, y - 2, x - 2, y - 1), b if not blink else (13, 37, 49)); rect(draw, (x + 2, y - 2, x + 3, y - 1), b if not blink else (13, 37, 49))
    elif motif == "snail":
        oval(draw, (x - 3, y - 5, x + 7, y + 6), a); oval(draw, (x, y - 2, x + 4, y + 3), (82, 45, 112)); rect(draw, (x - 8, y + 3, x + 5, y + 8), b)
        rect(draw, (x - 7, y - 1, x - 7, y + 4), b); rect(draw, (x - 4, y - 1, x - 4, y + 4), b); eye(draw, x - 8, y - 2, blink); eye(draw, x - 5, y - 2, blink)
    elif motif == "jar":
        rect(draw, (x - 6, y - 6, x + 6, y + 9), (60, 126, 126)); rect(draw, (x - 5, y - 5, x + 5, y + 8), (7, 28, 27)); rect(draw, (x - 5, y - 9, x + 5, y - 6), b)
        for dx, dy in ((-2, 0), (3, 3), (0, 6)): sparkle(draw, x + dx, y + dy - (frame + dx) % 2, a)
    elif motif == "bunny":
        oval(draw, (x - 5, y - 2, x + 5, y + 8), a); oval(draw, (x - 5, y - 10, x - 1, y), a); oval(draw, (x + 1, y - 10, x + 5, y), a)
        eye(draw, x - 3, y + 1, blink); eye(draw, x + 2, y + 1, blink); rect(draw, (x, y + 4, x, y + 4), b)
    elif motif == "tree":
        rect(draw, (x - 2, y + 2, x + 2, y + 13), (95, 55, 45)); oval(draw, (x - 10, y - 9, x + 9, y + 7), a)
        rect(draw, (x - 1, y + 1, x + 1, y + 5), b); oval(draw, (x - 3, y + 4, x + 3, y + 9), b); sparkle(draw, x, y + 6 + frame % 2, (255, 238, 157))
    elif motif == "flower":
        for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (3, -3)): oval(draw, (x + dx - 3, y + dy - 3, x + dx + 3, y + dy + 3), a)
        oval(draw, (x - 3, y - 3, x + 3, y + 3), b); rect(draw, (x, y + 6, x + 1, y + 13), (65, 150, 78))
    elif motif == "slime":
        oval(draw, (x - 7, y - 5, x + 7, y + 7), a); rect(draw, (x - 7, y + 1, x + 7, y + 7), a); eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink); rect(draw, (x - 1, y + 4, x + 1, y + 4), b)
    elif motif == "rocket":
        oval(draw, (x - 5, y - 9, x + 5, y + 6), a); draw.polygon(((x, y - 13), (x - 5, y - 5), (x + 5, y - 5)), fill=a); oval(draw, (x - 2, y - 4, x + 2, y), b)
        draw.polygon(((x - 5, y + 3), (x - 9, y + 9), (x - 4, y + 7)), fill=b); draw.polygon(((x + 5, y + 3), (x + 9, y + 9), (x + 4, y + 7)), fill=b)
        rect(draw, (x - 2, y + 7, x + 2, y + 12 + frame % 3), (255, 192, 68)); rect(draw, (x - 1, y + 10, x + 1, y + 15 + frame % 2), b)
    elif motif == "planet":
        oval(draw, (x - 6, y - 6, x + 6, y + 6), a); draw.arc((x - 11, y - 5, x + 11, y + 6), 10, 170, fill=b, width=2); draw.arc((x - 11, y - 5, x + 11, y + 6), 190, 350, fill=b, width=2)
    elif motif == "penguin":
        oval(draw, (x - 6, y - 7, x + 6, y + 9), (30, 32, 48)); oval(draw, (x - 4, y - 3, x + 4, y + 8), a); rect(draw, (x - 2, y - 1, x + 2, y + 1), b)
        eye(draw, x - 3, y - 3, blink); eye(draw, x + 2, y - 3, blink); rect(draw, (x - 6, y + 10, x - 2, y + 11), b); rect(draw, (x + 2, y + 10, x + 6, y + 11), b)
    elif motif == "fish":
        oval(draw, (x - 6, y - 4, x + 6, y + 4), a); draw.polygon(((x + 5, y), (x + 10, y - 5), (x + 10, y + 5)), fill=b); rect(draw, (x - 1, y - 4, x + 1, y + 4), b); eye(draw, x - 5, y - 1, blink)
    elif motif == "shelf":
        rect(draw, (x - 11, y + 7, x + 11, y + 9), b); oval(draw, (x - 6, y - 3, x + 6, y + 7), a); eye(draw, x - 3, y + 1, True); eye(draw, x + 2, y + 1, True); rect(draw, (x - 1, y + 4, x + 1, y + 4), (91, 42, 48))
    elif motif == "grape":
        for dx, dy in ((0, 0), (-4, 3), (4, 3), (-5, 7), (0, 7), (5, 7), (-3, 11), (3, 11), (0, 15)): oval(draw, (x + dx - 3, y + dy - 3, x + dx + 3, y + dy + 3), a)
        oval(draw, (x - 1, y - 7, x + 6, y - 2), b)
    elif motif == "campfire":
        oval(draw, (x - 6, y - 8, x + 6, y + 6), a); eye(draw, x - 3, y - 2, blink); eye(draw, x + 2, y - 2, blink)
        rect(draw, (x - 9, y + 8, x + 9, y + 10), (111, 65, 45)); draw.polygon(((x, y - 1), (x - 5, y + 8), (x + 5, y + 8)), fill=b); draw.polygon(((x, y + 2 - frame % 2), (x - 2, y + 8), (x + 2, y + 8)), fill=(255, 224, 74))
    elif motif == "curtain_cat":
        rect(draw, (x - 9, y - 9, x + 9, y + 10), (29, 18, 34)); rect(draw, (x - 9, y - 9, x - 5, y + 10), b); rect(draw, (x + 5, y - 9, x + 9, y + 10), b)
        oval(draw, (x - 5, y, x + 5, y + 9), (18, 18, 25)); rect(draw, (x - 5, y - 4, x - 2, y + 1), (18, 18, 25)); rect(draw, (x + 2, y - 4, x + 5, y + 1), (18, 18, 25)); eye(draw, x - 3, y + 3, blink); eye(draw, x + 2, y + 3, blink)
    elif motif == "hamster":
        oval(draw, (x - 7, y - 5, x + 7, y + 8), b); oval(draw, (x - 7, y - 7, x - 2, y - 2), b); oval(draw, (x + 2, y - 7, x + 7, y - 2), b); oval(draw, (x - 4, y, x + 4, y + 8), (248, 220, 176))
        eye(draw, x - 3, y - 1, blink); eye(draw, x + 2, y - 1, blink); rect(draw, (x, y + 3, x, y + 3), a)
    elif motif == "balloon":
        oval(draw, (x - 5, y - 10, x + 5, y + 3), a); draw.line((x, y + 3, x + (frame % 3 - 1), y + 13), fill=b); oval(draw, (x - 6, y + 10, x + 6, y + 20), b); eye(draw, x - 3, y + 14, blink); eye(draw, x + 2, y + 14, blink)
    elif motif == "cactus":
        oval(draw, (x - 5, y - 5, x + 5, y + 11), a); rect(draw, (x - 8, y, x - 4, y + 6), a); rect(draw, (x + 4, y - 1, x + 8, y + 5), a); oval(draw, (x - 3, y - 9, x + 3, y - 3), b)
        eye(draw, x - 3, y + 2, blink); eye(draw, x + 2, y + 2, blink)
    elif motif == "bat":
        oval(draw, (x - 4, y - 4, x + 4, y + 5), a); draw.polygon(((x - 3, y), (x - 10, y - 5 - int(flap)), (x - 8, y + 5), (x - 3, y + 3)), fill=a); draw.polygon(((x + 3, y), (x + 10, y - 5 - int(flap)), (x + 8, y + 5), (x + 3, y + 3)), fill=a)
        rect(draw, (x - 4, y - 7, x - 1, y - 2), a); rect(draw, (x + 1, y - 7, x + 4, y - 2), a); eye(draw, x - 3, y - 1, blink); eye(draw, x + 2, y - 1, blink)
    elif motif == "cupcake":
        draw.polygon(((x - 6, y + 2), (x + 6, y + 2), (x + 4, y + 11), (x - 4, y + 11)), fill=b); oval(draw, (x - 7, y - 6, x + 7, y + 5), a)
        eye(draw, x - 3, y, blink); eye(draw, x + 2, y, blink); sparkle(draw, x, y - 6, (255, 235, 93))


def render(scene: Scene, frame: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), scene.background)
    draw = ImageDraw.Draw(image)
    background(draw, scene, frame)
    bob = (0, -1, -1, 0, 0, 1, 1, 0)[frame]
    for index, y in enumerate((13, 47, 81, 115)):
        x = 10 if index % 2 == 0 else 21
        x += int(round(math.sin((frame + index * 2) * math.pi / 4) * 1.5))
        draw_motif(draw, scene.motif, x, y + bob * (1 if index % 2 == 0 else -1), frame + index, scene.primary, scene.secondary)
    return image


def save_gif(scene: Scene, output_path: Path):
    frames = [render(scene, frame) for frame in range(FRAME_COUNT)]
    palette_frames = [frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=64) for frame in frames]
    palette_frames[0].save(
        output_path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=DURATION_MS,
        loop=0,
        optimize=False,
        disposal=2,
    )


def preset_payload(scene: Scene) -> dict:
    return {
        "version": 2,
        "preset_id": scene.slug,
        "name": scene.name,
        "animation": "gif_animation",
        "category": scene.category,
        "description": scene.description,
        "tags": list(scene.tags),
        "palette": {
            "name": scene.name,
            "colors": ["#%02X%02X%02X" % scene.background, "#%02X%02X%02X" % scene.primary, "#%02X%02X%02X" % scene.secondary],
        },
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "params": {
            "plant_aware": True,
            "gif_directory": "assets/gifs",
            "gif_name": f"{scene.slug}.gif",
            "gif_index": 0,
            "playback_speed": 1.0,
            "brightness": 0.72,
            "brightness_mode": "rgb",
            "brightness_floor": 0.0,
            "gamma": 1.0,
            "flip_y": True,
            "fit_mode": "stretch",
            "contain_background": 0,
        },
    }


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.preset_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    for scene in SCENES:
        gif_path = args.output_dir / f"{scene.slug}.gif"
        preset_path = args.preset_dir / f"{scene.slug}.json"
        if gif_path.exists() and not args.overwrite:
            skipped += 1
        else:
            save_gif(scene, gif_path)
            written += 1
        preset_path.write_text(json.dumps(preset_payload(scene), indent=2) + "\n", encoding="utf-8")
    print(f"generated={written} skipped={skipped} presets={len(SCENES)} size={WIDTH}x{HEIGHT}")


if __name__ == "__main__":
    main()
