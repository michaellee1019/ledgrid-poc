"""Composer catalog and authored presets shared with the wall menu."""

from pathlib import Path
from typing import Any, Dict, List

from animation.core.component_catalog import ComponentCatalog
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.canopy_cup import CanopyCupAnimation
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.flame_burst import FlameBurstAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.fish_tank import FishTankAnimation
from animation.plugins.ocean_waves import OceanWavesAnimation
from animation.plugins.mini_metro import MiniMetroAnimation
from animation.plugins.cyclic_reef import CyclicReefAnimation
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.physarum_network import PhysarumNetworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.night_train_windows import NightTrainWindowsAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from animation.plugins.pixel_chase import PixelChaseAnimation
from animation.plugins.plant_glow import PlantGlowAnimation
from animation.plugins.gif_animation import GifAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.tetris import TetrisAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from animation.plugins.column_sequence import ColumnSequenceAnimation
from animation.plugins.circadian_window import CircadianWindowAnimation
from animation.plugins.cloud_canyon import CloudCanyonAnimation
from animation.plugins.desert_wind import DesertWindAnimation
from animation.plugins.moonlit_fog_banks import MoonlitFogBanksAnimation
from animation.plugins.rain_on_glass import RainOnGlassAnimation
from animation.plugins.tidal_bioluminescence import TidalBioluminescenceAnimation
from animation.plugins.waterfall_veil import WaterfallVeilAnimation
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation
from web.composer_component_presets import ComponentPresetCatalog


# The Gallery stays projected from the Scene v2 packet, while this small map
# supplies human-facing catalog metadata without reopening legacy discovery.
_COMPOSER_GALLERY_CLASSES = (
    AuroraCurtainsAnimation, CanopyCupAnimation, AsciiDropAnimation,
    EmojiAnimation, ChristmasTreeAnimation, NightTrainWindowsAnimation,
    ConwayLifeAnimation, TetrisAnimation, FireflySynchronyAnimation,
    FireworksAnimation, FlameBurstAnimation, FluidTankAnimation, FishTankAnimation,
    OceanWavesAnimation, MiniMetroAnimation, CyclicReefAnimation, LavaLampAnimation, SnakeAnimation, MazeChaseAnimation,
    PinballAnimation, PixelQuestAnimation, GradientAnimation, RainbowAnimation,
    SolidColorAnimation, SparkleAnimation, WaveAnimation, ColumnSequenceAnimation, CircadianWindowAnimation,
    CloudCanyonAnimation, DesertWindAnimation, MoonlitFogBanksAnimation,
    RainOnGlassAnimation, TidalBioluminescenceAnimation, WaterfallVeilAnimation,
    CellularTapestryAnimation, FlowFieldSilkAnimation, FrostworkAnimation,
    LivingStainedGlassAnimation, QuasicrystalBloomAnimation, LivingEcosystemAnimation,
    PhysarumNetworkAnimation, ReactionDiffusionGardenAnimation, WindInTheReedsAnimation,
)
_COMPOSER_GALLERY_METADATA = {
    item.COMPONENT_ID: {
        'name': str(getattr(item, 'ANIMATION_NAME', item.COMPONENT_ID.replace('_', ' ').title())),
        'description': str(getattr(item, 'ANIMATION_DESCRIPTION', 'A live Scene animation.')),
    }
    for item in _COMPOSER_GALLERY_CLASSES
}


def composer_presets(project_root: Path) -> ComponentPresetCatalog:
    return ComponentPresetCatalog(
        project_root,
        {
            AuroraCurtainsAnimation.COMPONENT_ID: AuroraCurtainsAnimation._normalized_parameters,
            CanopyCupAnimation.COMPONENT_ID: CanopyCupAnimation._normalized_parameters,
            AsciiDropAnimation.COMPONENT_ID: AsciiDropAnimation._normalized_parameters,
            EmojiAnimation.COMPONENT_ID: EmojiAnimation._normalized_parameters,
            ChristmasTreeAnimation.COMPONENT_ID: ChristmasTreeAnimation._normalized_parameters,
            ClockOverlayAnimation.COMPONENT_ID: ClockOverlayAnimation._normalized_parameters,
            NightTrainWindowsAnimation.COMPONENT_ID: NightTrainWindowsAnimation._normalized_parameters,
            ConwayLifeAnimation.COMPONENT_ID: ConwayLifeAnimation._normalized_parameters,
            TetrisAnimation.COMPONENT_ID: TetrisAnimation._normalized_parameters,
            FireflySynchronyAnimation.COMPONENT_ID: FireflySynchronyAnimation._normalized_parameters,
            FireworksAnimation.COMPONENT_ID: FireworksAnimation._normalized_parameters,
            FlameBurstAnimation.COMPONENT_ID: FlameBurstAnimation._normalized_parameters,
            FluidTankAnimation.COMPONENT_ID: FluidTankAnimation._normalized_parameters,
            FishTankAnimation.COMPONENT_ID: FishTankAnimation._normalized_parameters,
            OceanWavesAnimation.COMPONENT_ID: OceanWavesAnimation._normalized_parameters,
            MiniMetroAnimation.COMPONENT_ID: MiniMetroAnimation._normalized_parameters,
            CyclicReefAnimation.COMPONENT_ID: CyclicReefAnimation._normalized_parameters,
            LavaLampAnimation.COMPONENT_ID: LavaLampAnimation._normalized_parameters,
            SnakeAnimation.COMPONENT_ID: SnakeAnimation._normalized_parameters,
            MazeChaseAnimation.COMPONENT_ID: MazeChaseAnimation._normalized_parameters,
            PinballAnimation.COMPONENT_ID: PinballAnimation._normalized_parameters,
            PixelQuestAnimation.COMPONENT_ID: PixelQuestAnimation._normalized_parameters,
            PixelChaseAnimation.COMPONENT_ID: PixelChaseAnimation._normalized_parameters,
            PlantGlowAnimation.COMPONENT_ID: PlantGlowAnimation._normalized_parameters,
            GifAnimation.COMPONENT_ID: GifAnimation._normalized_parameters,
            GradientAnimation.COMPONENT_ID: GradientAnimation._normalized_parameters,
            RainbowAnimation.COMPONENT_ID: RainbowAnimation._normalized_parameters,
            SolidColorAnimation.COMPONENT_ID: SolidColorAnimation._normalized_parameters,
            SparkleAnimation.COMPONENT_ID: SparkleAnimation._normalized_parameters,
            WaveAnimation.COMPONENT_ID: WaveAnimation._normalized_parameters,
            CircadianWindowAnimation.COMPONENT_ID: CircadianWindowAnimation._normalized_parameters,
            CloudCanyonAnimation.COMPONENT_ID: CloudCanyonAnimation._normalized_parameters,
            DesertWindAnimation.COMPONENT_ID: DesertWindAnimation._normalized_parameters,
            MoonlitFogBanksAnimation.COMPONENT_ID: MoonlitFogBanksAnimation._normalized_parameters,
            RainOnGlassAnimation.COMPONENT_ID: RainOnGlassAnimation._normalized_parameters,
            TidalBioluminescenceAnimation.COMPONENT_ID: TidalBioluminescenceAnimation._normalized_parameters,
            WaterfallVeilAnimation.COMPONENT_ID: WaterfallVeilAnimation._normalized_parameters,
            CellularTapestryAnimation.COMPONENT_ID: CellularTapestryAnimation._normalized_parameters,
            FlowFieldSilkAnimation.COMPONENT_ID: FlowFieldSilkAnimation._normalized_parameters,
            FrostworkAnimation.COMPONENT_ID: FrostworkAnimation._normalized_parameters,
            LivingStainedGlassAnimation.COMPONENT_ID: LivingStainedGlassAnimation._normalized_parameters,
            QuasicrystalBloomAnimation.COMPONENT_ID: QuasicrystalBloomAnimation._normalized_parameters,
            LivingEcosystemAnimation.COMPONENT_ID: LivingEcosystemAnimation._normalized_parameters,
            PhysarumNetworkAnimation.COMPONENT_ID: PhysarumNetworkAnimation._normalized_parameters,
            ReactionDiffusionGardenAnimation.COMPONENT_ID: ReactionDiffusionGardenAnimation._normalized_parameters,
            WindInTheReedsAnimation.COMPONENT_ID: WindInTheReedsAnimation._normalized_parameters,
        },
    )


def gallery_entries(catalog: ComponentCatalog, presets: ComponentPresetCatalog) -> List[Dict[str, Any]]:
    """Project the current Scene v2 animations in Composer's display order."""
    entries: List[Dict[str, Any]] = []
    for descriptor in catalog.descriptors:
        if descriptor.role.value != 'animation':
            continue
        component_id = descriptor.component_id
        metadata = _COMPOSER_GALLERY_METADATA.get(component_id, {})
        try:
            preset_count = len(presets.choices(component_id))
        except ValueError:
            preset_count = 0
        entries.append({
            'key': f'{descriptor.provider.value}:{component_id}:v{descriptor.version}',
            'component_id': component_id,
            'provider': descriptor.provider.value,
            'version': descriptor.version,
            'role': descriptor.role.value,
            'name': metadata.get('name', component_id.replace('_', ' ').title()),
            'description': metadata.get('description', 'A live Scene animation.'),
            'parameters': descriptor.default_parameters(),
            'available': True,
            'preset_count': preset_count,
        })
    entries.sort(key=lambda item: (str(item['name']).casefold(), str(item['key'])))
    return entries
