# Procedural animation ideas

This list is intentionally aimed beyond another gradient, rainbow, sparkle, sprite loop, or arcade game. The current library already covers those well, along with clocks, Conway's Life, fireworks, a fluid tank, a living ecosystem, and several autoplaying games. The best additions now are slow, layered systems whose behavior is generated continuously and whose loops are difficult to perceive.

## Design target

The physical canvas is unusually tall: 32 strips by 138 LEDs (4,416 pixels). New work should compose for that shape rather than placing a small conventional screen in its center. Falling, rising, stratified, and vertical-parallax scenes are especially promising.

Every idea below should have an ambient-first default:

- A dark floor with broad regions of low-to-medium light, restrained highlights, no full-wall white flashes, and no strobing.
- Slow macro motion with enough fine motion to feel alive. Most effects only need a 20–40 FPS source rate even though the manager runs at 200 Hz.
- A seeded, continuously evolving simulation with a long reset interval or seamless phase evolution. Ten minutes without an obvious repeat is a useful minimum target.
- Separate simulation and rendering controls. Changing glow, palette, trails, or brightness should not alter the underlying behavior.
- A `mood` or palette family, a `motion` control, a `density` control where appropriate, and a conservative global brightness default.
- Optional plant-aware rendering using `config/plant_pixel_map.json`. The real plants can become silhouettes, obstacles, light filters, habitats, or foreground cover.
- A bounded fixed-step simulation and vectorized rendering into reusable buffers. Expensive scenes should update their simulation at 5–20 Hz, interpolate or advect presentation at 20–40 FPS, and return `changed=False` between source ticks.
- No required network or sensor dependency. Data-aware and interactive effects should retain a convincing autonomous fallback.

## Best first builds

| Priority | Idea | Why it belongs on this wall | Effort |
| --- | --- | --- | --- |
| 1 | Rain on Glass | Uses the full height, reads immediately, and can be both mesmerizing and genuinely restful | Medium |
| 2 | Reaction-Diffusion Garden | Produces endless organic detail from a compact NumPy simulation and is unlike anything currently shipped | Medium |
| 3 | Aurora Curtains | High visual payoff, excellent ambient fill, and a natural match for the tall aspect ratio | Medium |
| 4 | Wind in the Reeds | Turns the physical plant mask into part of the artwork instead of treating it as an obstruction | Medium |
| 5 | Firefly Synchrony | A simple agent system creates a strong emergent story without becoming visually busy | Medium |
| 6 | Physarum Network | Slow network growth, path finding, pruning, and luminous transport fully exploit arbitrary Python state | High |
| 7 | Cellular Tapestry | The 138-pixel height becomes a visible history of a tiny rule evolving over time | Small |
| 8 | Circadian Window | Useful all-day ambient light whose scene and color temperature evolve with real time | Medium |
| 9 | Living Stained Glass | Broad luminous regions make excellent room light while the underlying geometry evolves subtly | Medium |
| 10 | Waterfall Veil | Makes spectacular use of the height and can remain calm through low contrast and sparse highlights | Medium |

## Atmospheres and natural phenomena

### 1. Rain on Glass

Large droplets nucleate near the top, accelerate, leave refractive trails, merge when they meet, and occasionally split after hitting an old track. A blurred city-light or moonlit-garden field behind the glass is displaced by each droplet, so the scene feels optically wet rather than like falling blue lines.

- **Python leverage:** sparse droplet physics, trail persistence, spatial buckets for merging, and a NumPy displacement/refraction field.
- **Ambient behavior:** long quiet intervals, rare heavier showers, low blue/amber background glow, and no lightning by default.
- **Useful controls:** drizzle/storm intensity, glass persistence, background scene, wind, refraction strength, and plant-aware rain shadows.

### 2. Aurora Curtains

Several translucent curtains fold independently from bottom to top, with slow traveling ripples, faint stars behind them, and occasional brighter knots flowing along a curtain. This should look volumetric: overlapping sheets add light while dark gaps retain depth.

- **Python leverage:** layered divergence-free flow fields, octave noise, phase-advection, and additive palette compositing.
- **Ambient behavior:** very slow global drift, muted boreal colors, bounded peak luminance, and minute-scale changes in activity.
- **Useful controls:** curtain count, solar activity, vertical reach, shimmer, star density, wind direction, and palettes such as boreal, violet, ember, and moon-white.

### 3. Cloud Canyon

Soft banks of fog rise through the tall wall while moonlight or sunset light leaks between them. Dense clouds should occlude light rather than merely tint it; thin edges catch silver or peach highlights and reveal slow internal billowing.

- **Python leverage:** advected multi-octave density fields, cheap Beer-Lambert-style light attenuation, and cached low-resolution simulation upsampling.
- **Ambient behavior:** broad diffuse fill with almost no sharp motion; a full transition from clear to overcast can take 10–20 minutes.
- **Useful controls:** cloud cover, light elevation, wind layers, contrast, color temperature, and day/night mood.

### 4. Waterfall Veil

Fine streams descend at different speeds, collide with ledges, fragment into mist, and collect briefly in luminous pools before spilling again. The scene can imply a cliff with only a few pixels of rock and use the entire height for falling motion.

- **Python leverage:** height-field terrain, particles constrained by that field, water flux, foam lifetime, mist advection, and simple pool conservation.
- **Ambient behavior:** mostly dark slate and forest tones with cool moving highlights; no uniform bright water column.
- **Useful controls:** flow rate, ledge complexity, mist, pool glow, moon/sunset lighting, and plant-aware foreground occlusion.

### 5. Tidal Bioluminescence

A dark ocean occupies the lower wall. Slow swells disturb microscopic plankton, producing cyan points and curling luminous wakes that fade over several seconds. Rare deep silhouettes can pass through and trigger wider blooms without turning the scene into a literal aquarium.

- **Python leverage:** a one-dimensional wave surface coupled to a two-dimensional concentration/advection field and sparse wake emitters.
- **Ambient behavior:** near-black navy base, gentle wave rhythm, small light budget, and rare rather than constant spectacles.
- **Useful controls:** tide strength, plankton density, wake frequency, persistence, depth haze, and cyan/green/violet palettes.

### 6. Wind in the Reeds

Procedural stems grow up from the bottom and bend in coherent gusts. In plant-aware mode, calibrated real foliage becomes the foreground canopy: light pools behind it, gusts appear to pass through it, and small motes shelter in its lee.

- **Python leverage:** inverse-kinematic stem chains, a spatial wind field with gust fronts, and mask-derived occlusion/distance fields.
- **Ambient behavior:** warm dusk or cool moonlight, synchronized but imperfect swaying, and long lulls between gusts.
- **Useful controls:** wind, gustiness, stem density, season, motes, silhouette strength, and palette.

### 7. Moonlit Fog Banks

Layered fog drifts upward through a forest or mountain silhouette while a hidden moon moves behind it. The moon is rarely shown directly; it reveals itself through halos, soft shafts, and silver edges.

- **Python leverage:** signed-distance silhouettes, low-resolution semi-Lagrangian advection, depth-layer compositing, and mask-aware light scattering.
- **Ambient behavior:** extremely slow, low-saturation motion with a stable dark floor and optional warm pre-dawn transition.
- **Useful controls:** fog depth, moon phase, tree/mountain geometry, drift direction, halo size, and contrast.

### 8. Desert Wind

Layered dunes evolve through subtle erosion while thin ribbons of sand skim their crests. Light moves gradually from violet predawn through amber sunset, allowing the same geometry to feel different across a long session.

- **Python leverage:** one-dimensional dune height evolution, particle saltation, slope relaxation, and procedural lighting from surface normals.
- **Ambient behavior:** large calm areas of warm light, very sparse moving grains, and hour-scale palette transitions.
- **Useful controls:** wind, grain activity, sun angle, dune scale, haze, and palettes including ochre, Mars, moonlit, and rose.

## Emergent living systems

### 9. Reaction-Diffusion Garden

Gray-Scott reaction-diffusion chemistry slowly grows spots, stripes, coral branches, fingerprints, and cell-like islands. Older regions can shift palette or become substrate for a second reagent, creating a garden with history rather than an endlessly scrolling texture.

- **Python leverage:** vectorized Laplacian updates, multiple feed/kill regimes, seeded disturbances, and age/composition fields.
- **Ambient behavior:** simulation at roughly 8–15 Hz, slow pattern formation, dark substrate, and luminous edges rather than solid bright blobs.
- **Useful controls:** morphology, growth rate, seeding mode, edge glow, color-by-age, perturbation interval, and plant-mask interaction.

### 10. Physarum Network

Thousands of tiny agents explore a nutrient field, reinforce useful routes, abandon inefficient ones, and form a glowing transport network. Nutrient sites can migrate slowly so the network continuously adapts without needing a hard reset.

- **Python leverage:** agent sensing/steering, pheromone deposition and diffusion, resource consumption, pruning, and bounded spatial updates.
- **Ambient behavior:** most pixels remain dark; a small number of thick routes pulse slowly as virtual material moves through them.
- **Useful controls:** agent count, branching, diffusion, nutrient layout, pulse visibility, palette, and whether plant-covered areas attract or repel growth.

### 11. Murmuration

A flock of points compresses into ribbons, sheets, and vortices as invisible pressure waves move through it. Occasionally the flock parts around an obstacle or follows a soft light source, then settles back into a quiet formation.

- **Python leverage:** boids with a spatial hash, curl-noise steering, obstacle avoidance, projection trails, and depth-based brightness.
- **Ambient behavior:** restrained density and short, soft trails; dramatic shape changes happen infrequently and never flash the entire wall.
- **Useful controls:** flock size, cohesion, turbulence, trail length, altitude bias, predator events, and dusk/pearl/neon palettes.

### 12. Firefly Synchrony

Individual fireflies wander around a dark meadow and adjust their internal clocks when nearby neighbors flash. Local clusters gradually synchronize, compete with other rhythms, fall apart in wind, and assemble again.

- **Python leverage:** Kuramoto-style coupled oscillators, neighbor lookup, agent motion, local wind, and energy/recovery cycles.
- **Ambient behavior:** soft sub-second pulses distributed across the wall, with a hard cap on the fraction allowed to peak together.
- **Useful controls:** population, coupling radius, synchrony, wandering, pulse softness, meadow glow, and plant-mask habitat preference.

### 13. Cyclic Reef

A multi-state cyclic cellular automaton creates waves of competing color that resemble coral polyps, microscopic organisms, or animated mineral bands. Small grazing agents can open dark channels that the reef slowly recolonizes.

- **Python leverage:** vectorized neighborhood transitions, multiple species rules, local mutation, age fields, and optional mobile disruptors.
- **Ambient behavior:** low update rate, limited palette, gentle crossfades between states, and broad dark cavities for contrast.
- **Useful controls:** state count, threshold, mutation, grazer density, edge glow, palette, and topology.

### 14. Frostwork

Ice crystals grow from the borders through diffusion-limited aggregation, branch into empty space, catch blue-white light, then sublimate and regrow from a new cold front. Growth history determines color and sparkle intensity.

- **Python leverage:** random walkers accelerated by occupancy distance/bounds, branch aging, heat diffusion, and controlled melt fronts.
- **Ambient behavior:** growth is slow enough to watch over minutes; sparkle is localized to newly formed tips and never random full-field glitter.
- **Useful controls:** temperature, branching bias, growth speed, melt cycle, crystal color, and border/seed placement.

### 15. Mycelial Pulse

A subterranean network grows between dim resource nodes, branches, fuses with itself, reroutes after damage, and sends visible nutrient pulses along mature paths. Fruiting bodies can appear rarely near the top of the network and fade after a short season.

- **Python leverage:** graph growth guided by a scalar field, path reinforcement, loop formation, resource transport, and graph-distance pulse propagation.
- **Ambient behavior:** slow amber or blue-green veins on a nearly black ground, with rare brighter transfers instead of constant motion.
- **Useful controls:** resource density, branching, transport rate, season length, fruiting frequency, and woodland/alien palettes.

### 16. Lichen Colonies

Several differently colored colonies spread over a virtual stone surface. They compete at boundaries, retreat during dry periods, bloom after moisture passes, and leave mineral stains that influence future growth.

- **Python leverage:** competing growth fronts, moisture and nutrient diffusion, dormancy, and persistent substrate memory.
- **Ambient behavior:** extremely slow evolution with muted mineral colors; meaningful changes occur over minutes rather than frames.
- **Useful controls:** colony count, climate cycle, competition, surface roughness, edge illumination, and palette.

### 17. Rootlight

Roots descend and fork from plant-mask regions, seeking drifting pockets of water and minerals. Successful routes thicken and carry warm pulses back upward; unused branches slowly recede. With the real plants above them, the wall becomes an imaginary cross-section of the same habitat.

- **Python leverage:** tropism-driven branching, resource fields, collision and anastomosis, transport along a dynamic tree/graph, and mask-derived root origins.
- **Ambient behavior:** dark soil, sparse golden or cyan pulses, and growth measured in minutes.
- **Useful controls:** root density, branching, resource renewal, gravity bias, pulse rate, soil palette, and plant-mask coupling.

### 18. Particle Life

Several particle species attract and repel one another at different distances. They spontaneously form cells, chains, orbiting colonies, migrating blobs, and dissolving membranes without any authored choreography.

- **Python leverage:** species interaction matrices, grid-based neighbor acceleration, stable integration, cluster metrics, and periodic bounded perturbations.
- **Ambient behavior:** low particle count, soft splats and trails, slow forces, and palettes that prevent every species from competing at full brightness.
- **Useful controls:** species count, interaction family, population, viscosity, trail decay, mutation interval, and palette.

## Mathematical light sculptures

### 19. Flow-Field Silk

Fine luminous threads are released into a smooth vector field and weave around one another as the field changes. Threads can fray, braid, vanish into shadow, and be replaced upstream, producing the feel of silk in water rather than a generic noise texture.

- **Python leverage:** curl-noise or analytic vector fields, streamline integration, trail accumulation, and anti-aliased line rasterization.
- **Ambient behavior:** a small number of slow threads over a broad colored shadow field; motion remains coherent and directional.
- **Useful controls:** thread count, field family, turbulence, persistence, width, direction, and palette.

### 20. Strange Attractor Observatory

Lorenz, Rössler, Clifford, and custom attractors are projected into the wall as slowly rotating constellations. Dense regions glow like nebulae while a bright tracer reveals the live trajectory.

- **Python leverage:** numerical integration, projection/camera transforms, density accumulation, histogram tone mapping, and attractor morphing.
- **Ambient behavior:** dim accumulated clouds with one restrained tracer; camera movement takes minutes rather than seconds.
- **Useful controls:** attractor, integration speed, viewpoint drift, decay, tracer visibility, symmetry, and palette.

### 21. Lava-Lamp Metaballs

Buoyant blobs heat near the bottom, rise, merge, cool, and sink. Their implicit surfaces blend smoothly, with a warm inner core and soft halo that turns the wall into a tall, modern lava lamp.

- **Python leverage:** particle buoyancy, temperature exchange, metaball scalar fields, contour bands, and volume-conserving split/merge events.
- **Ambient behavior:** very slow motion, a dark background, and large rounded regions that cast useful colored light without visual noise.
- **Useful controls:** blob count, viscosity, heat, merge tendency, halo, glass tint, and classic/ice/forest palettes.

### 22. Living Stained Glass

Large Voronoi cells drift almost imperceptibly while their shared edges relax toward a balanced tessellation. Light wanders between panes as if clouds were passing outside; occasional cells divide or merge and the lead network heals around them.

- **Python leverage:** Voronoi or nearest-seed fields, Lloyd relaxation, adjacency graphs, cell lifecycle, and procedural transmitted light.
- **Ambient behavior:** broad stable panes make excellent room illumination, while structural changes are rare and gentle.
- **Useful controls:** pane count, geometry drift, lead width, light direction, color harmony, division rate, and cathedral/sea/amber palettes.

### 23. Chladni Sand

Virtual grains migrate toward the nodal lines of a vibrating plate. As frequencies slowly crossfade, crisp geometric figures loosen into clouds and reorganize into new symmetries.

- **Python leverage:** modal wave equations, gradients of vibration energy, particle relaxation, and interpolation between compatible modes.
- **Ambient behavior:** modes hold for minutes, transitions are gradual, and only the settled sand and a faint plate glow are illuminated.
- **Useful controls:** mode family, transition time, grain count, damping, plate glow, symmetry, and sand/light palette.

### 24. Pendulum Wave

Rows of virtual pendulums with carefully related periods drift in and out of phase, forming traveling waves, braids, checkerboards, and moments of total alignment. Trails turn the motion into a spatial light sculpture.

- **Python leverage:** exact oscillator phase relationships, perspective projection, trail persistence, and automatic selection of visually interesting recurrence windows.
- **Ambient behavior:** predictable, slow, and hypnotic; alignment events brighten locally rather than flashing the whole grid.
- **Useful controls:** oscillator count, recurrence period, trail, perspective, bob size, and palette.

### 25. Moiré Loom

Two or three translucent line lattices rotate, bend, and breathe at slightly different rates. Their interference creates large traveling shapes much more complex than any input layer, like woven light viewed through moving fabric.

- **Python leverage:** analytic line-distance fields, nonlinear warps, phase locking, and gamma-correct layer compositing.
- **Ambient behavior:** low contrast and slow sub-degree motion prevent shimmer from becoming harsh; the macro pattern does most of the work.
- **Useful controls:** layer count, weave geometry, spacing, warp, rotation rates, edge softness, and palette.

### 26. Magnetic Flux

Drifting magnetic poles generate curved field lines. Charged sparks spiral briefly along those lines, poles split or annihilate, and iron-filament textures turn to follow the changing field.

- **Python leverage:** vector field evaluation, streamline tracing, pole dynamics, charged-particle integration, and line-density rendering.
- **Ambient behavior:** mostly dark field lines with brief low-energy travelers; pole changes happen slowly and preserve visual continuity.
- **Useful controls:** pole count, field strength, tracer rate, line density, drift, polarity colors, and glow.

### 27. Quasicrystal Bloom

Several plane waves interfere to create non-repeating five-, eight-, ten-, or twelve-fold symmetries. The phase relationships drift slowly, making rosettes open, tunnel inward, and rearrange without translating like an ordinary wave.

- **Python leverage:** vectorized wave superposition, symmetry sets, nonlinear palette mapping, phase modulation, and optional domain warping.
- **Ambient behavior:** broad low-frequency structures, slow phase drift, and palette bands with restrained contrast.
- **Useful controls:** symmetry order, spatial scale, phase speed, warp, band softness, center drift, and palette.

### 28. Cellular Tapestry

A one-dimensional cellular automaton writes one new row at a time, so the wall's height becomes a moving historical record. Multiple rules can crossfade or exchange boundary conditions, producing lace, triangles, woven bands, and surprising pseudo-random structures.

- **Python leverage:** bitwise rule evaluation, mutation events, rule scheduling, age-based coloring, and deterministic seeded replay.
- **Ambient behavior:** only one row is born at a time; the tapestry scrolls slowly and old rows fade into a dark textile background.
- **Useful controls:** rule or rule playlist, row interval, mutation, wrap, color-by-state/age, fade length, and palette.

## Long-form scenes and responsive installations

### 29. Circadian Window

A procedural sky tracks local time from deep night through dawn, daylight, sunset, and twilight. Stars rotate, clouds inherit the current wind, the moon follows a plausible arc, and artificial window light appears after dark.

- **Python leverage:** solar/lunar position approximations, continuous color-temperature curves, star coordinates, cloud simulation, and isolated time providers for testing.
- **Ambient behavior:** its primary purpose is pleasant all-day room light; changes are nearly imperceptible moment to moment.
- **Useful controls:** latitude/longitude or simple offset, time scale, cloud cover, horizon style, stars, interior warmth, and brightness schedule.

### 30. City at Dusk

A vertical city assembles itself from procedural towers, windows, rooftop gardens, signs, and elevated bridges. Rooms turn on and off according to small occupancy models, elevators travel between floors, and the sky completes a slow day/night cycle.

- **Python leverage:** shape grammar generation, building adjacency, per-room schedules, tiny agent stories, parallax haze, and deterministic city seeds.
- **Ambient behavior:** windows are warm isolated pools in a large blue-black scene; signs are subdued and traffic is sparse.
- **Useful controls:** architecture style, city density, time of day, occupancy, weather, sign activity, and window palette.

### 31. Night Train Windows

The wall becomes a passing landscape seen through a train: distant mountains move slowly, trees and utility poles cross faster, window reflections hover in front, and small towns occasionally appear and disappear.

- **Python leverage:** seeded procedural terrain, multi-layer parallax, object spawning from a route grammar, weather, and reflection compositing.
- **Ambient behavior:** long stretches of quiet darkness punctuated by warm towns; speed can be slow enough for sleep-friendly use.
- **Useful controls:** route type, train speed, weather, moon, town frequency, reflection strength, and night/sunrise palettes.

### 32. Terrarium Cross-Section

Above ground, leaves open and close through a day cycle while condensation forms on glass. Below ground, moisture moves through soil, roots grow, fungi connect them, and tiny burrowers reshape a few passages.

- **Python leverage:** coupled but low-rate moisture, root, fungal, and agent simulations with a day/night driver and persistent world state.
- **Ambient behavior:** the scene is mostly earthy darkness and green-gold pools; subterranean events are subtle discoveries rather than constant action.
- **Useful controls:** humidity, season, plant density, underground visibility, creature activity, lifecycle length, and realistic/fantasy palettes.

### 33. Deep-Space Survey

A virtual telescope slowly scans a procedurally generated sky. Star fields reveal nebular dust, gravitational-lens arcs, variable stars, and rare comets; exposures accumulate detail and then slew gently to a new target.

- **Python leverage:** seeded star catalogs, point-spread functions, layered fractal dust, exposure accumulation, orbital paths, and camera scheduling.
- **Ambient behavior:** sparse pinpoints over a deep colored black with very slow camera motion and no rapid hyperspace streaks.
- **Useful controls:** target family, exposure time, star density, telescope drift, labels off/on, palette, and event rarity.

### 34. Lantern Weather

Paper lanterns float at different depths and respond to a simulated breeze. Their flames breathe independently, lanterns shelter one another from gusts, and the sky treatment reflects current or fictional weather.

- **Python leverage:** simple rigid-body sway, thermal lift, flock-like spacing, wind fields, paper-light transmission, and optional weather input.
- **Ambient behavior:** warm pools of light dominate; movement is slow, collisions are soft, and storm mode remains dim rather than flashy.
- **Useful controls:** lantern count, wind, lift, depth, paper colors, weather source, and launch frequency.

### 35. Lunar Tide Clock

Instead of displaying digits, the wall shows time through a rising and falling luminous tide, the moon's phase and altitude, and small orbital markers. It works as an abstract clock first and a scientifically inspired ambient piece second.

- **Python leverage:** time and lunar-phase calculations, harmonic tide approximation, wave rendering, and an overridable clock/data adapter.
- **Ambient behavior:** minute-rate semantic updates with smooth wave interpolation and a stable cool nighttime light envelope.
- **Useful controls:** location/offset, real or accelerated time, tide exaggeration, moon size, markers, palette, and optional compact time readout.

### 36. Solar Weather

The wall shows a stylized sun surface with convection cells, prominences, magnetic loops, and occasional ejections. It can run entirely procedurally or gently bias activity from current public solar indices when a data adapter is available.

- **Python leverage:** evolving cellular convection, magnetic-field arcs, edge plasma particles, seeded event scheduling, caching, and optional API normalization.
- **Ambient behavior:** deep ember and burgundy rather than full yellow-white; rare events unfold over seconds with no flash cut.
- **Useful controls:** activity, convection scale, prominence rate, rotation, corona strength, data mode, and ember/ultraviolet palettes.

### 37. Presence-Responsive Shoal

A calm abstract shoal swims through dark water autonomously. If a local camera, distance sensor, or dashboard pointer is later provided, the fish-like agents gather near slow movement, scatter from abrupt movement, and then settle naturally.

- **Python leverage:** boids, obstacle and silhouette avoidance, spatial indexing, input filtering, and a fully autonomous virtual-observer fallback.
- **Ambient behavior:** silhouettes and soft wake light rather than bright cartoon fish; response is damped to avoid twitchiness.
- **Useful controls:** population, curiosity, startle response, wake strength, depth, input source, and palette.

### 38. Audio-Reactive Hush

This is the opposite of a nightclub visualizer. A slow ambient field listens only for broad energy, rhythm, and spectral balance: conversation warms nearby regions, bass gently bends the field, and quiet lets it return to an idle breathing state.

- **Python leverage:** streaming FFT bands, automatic gain control, beat confidence, envelope followers, input health monitoring, and procedural fallback signals.
- **Ambient behavior:** strict rate-of-change and brightness limits; transients cannot create flashes, and the idle mode is complete on its own.
- **Useful controls:** sensitivity, response time, spectral mapping, calmness limit, privacy-preserving local input, fallback mode, and palette.

## Shared building blocks worth adding

Several of these can share infrastructure without collapsing into one giant plugin:

- A cached logical `(height, width)` coordinate/mapping helper with consistent vertical orientation and optional serpentine conversion.
- A reusable plant-mask loader that exposes Boolean, distance, edge, and blurred-occlusion fields.
- Allocation-free glow, trail-decay, soft-point, anti-aliased line, and gamma-aware additive compositing primitives.
- A seeded 2D value/curl-noise field implemented with NumPy and cached geometry, avoiding a new heavy dependency for every plugin.
- A fixed-step simulation helper with bounded catch-up, source-rate render throttling, and a deterministic reset/reseed policy.
- Small time, weather, audio, and presence adapters whose last-known/autonomous fallback behavior is explicit and testable.
- Palette LUTs with a luminance cap, so visually different presets retain roughly comparable room brightness and power draw.

## Selection test for future implementation

An idea is ready to build when it has a clear answer to all of these:

1. What changes every simulation step, what changes only on an event, and what is presentation-only?
2. What makes the result meaningfully better than a pre-rendered GIF or a moving gradient?
3. How does it use the 32×138 shape or the real plant mask?
4. What are the quiet default, showcase preset, and low-brightness nighttime preset?
5. How does it remain interesting for at least ten minutes without becoming hectic?
6. What work is bounded at maximum density, and can the default path stay below the repository's 4 ms plugin p95 budget on the desktop benchmark?
7. Which deterministic simulation assertions and representative rendered frames will prove that it behaves and looks as intended?
