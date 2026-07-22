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
- Optional composable plant modifiers using the calibrated foliage map at `config/plant_pixel_map_32x138.json` and the seven-region globe map at `config/plant_globe_map_32x138.json`. With every modifier off, the animation must behave and render exactly as if plant integration did not exist.
- A bounded fixed-step simulation and vectorized rendering into reusable buffers. Expensive scenes should update their simulation at 5–20 Hz, interpolate or advect presentation at 20–40 FPS, and return `changed=False` between source ticks.
- No required network or sensor dependency. Data-aware and interactive effects should retain a convincing autonomous fallback.

## Composable plant modifiers

The current global `plant_aware` switch asks every plugin to hide a different,
preselected behavior behind one bit. The result is technically plant-aware but
operationally opaque: depending on the active animation, the same switch may
move content, block movement, bend a field, create a habitat, light a landmark,
or turn a globe into a bumper.

Replace that boolean model with an explicit manager-global
`PlantModifierState`. Operators should be able to say what the physical plants
mean to the current scene. Plugins remain responsible for a native,
animation-specific interpretation; the framework owns state, validation,
geometry, caching, control authority, and observability.

### State and authority

The persisted and API-facing shape should be equivalent to:

```json
{
  "version": 1,
  "active": ["illuminate", "obstacle"],
  "strengths": {
    "illuminate": 0.5,
    "obstacle": 1.0
  }
}
```

- `active` is a unique set of modifier IDs. An empty set is the canonical off
  state and must preserve both pixels and semantic evolution exactly.
- Every modifier has a dashboard toggle and a normalized `0.0`–`1.0` strength.
  Enabling a modifier for the first time uses `0.5`, except `obstacle`, which
  uses `1.0`. Plugins map the normalized value into bounded domain-specific
  behavior; they must document that mapping in their schema or support metadata.
- Visual modifiers and `emitter` are stackable. At most one field modifier and
  at most one surface role may be active. Invalid combinations are rejected at
  the manager/API boundary rather than silently resolved by individual plugins.
- The manager applies the global state live to the active animation and injects
  it into every future start and preview. Presets may carry a recommended state,
  but may not override an operator's global selection.
- A plugin declares `PLANT_MODIFIER_SUPPORT` as a set of stable modifier IDs.
  Unsupported active modifiers are ignored without changing the plugin's
  simulation or frame, and are exposed in status and the dashboard as
  unsupported for the active animation.
- Direct or headless plugin construction defaults to an empty state. Shared
  helpers such as `plant_modifier_enabled(id)` and
  `plant_modifier_strength(id)` keep disabled guards consistent and testable.

### Semantic geometry

Foliage and rooting globes remain different installation layers. Their targets
are fixed by modifier semantics rather than exposed as another matrix of
operator controls.

- Foliage is a soft, irregular, occluding layer suitable for shadow, habitat,
  growth, forces, and terrain.
- The seven globes are solid landmarks with stable region IDs. They are the only
  portal and bumper nodes.
- Exact masks define contact, illumination cores, and boundaries. Dilated
  clearance is used for routing, spawning, HUD placement, and influence zones.
- The shared cache should add per-layer edges, distance/normal fields, and named
  globe-region masks to its existing flat/logical foliage, globe, obstacle,
  clearance, and safe-space views. Cache keys must continue to include geometry,
  mask paths, and clearance.
- Missing or malformed calibration data yields empty geometry plus an observable
  error. It must never create half-applied simulation changes, unbounded safe
  placement searches, or a stale cached mask.

### Modifier catalog

| Group | Modifier | Fixed target | Observable meaning | Strength meaning | Example |
| --- | --- | --- | --- | --- | --- |
| Visual, stackable | **Illuminate** (`illuminate`) | Foliage and globes | Increase the luminance of existing plant pixels while preserving the scene's hue; when combined with Shadow, illuminate boundaries rather than refilling the dark core | Gain and halo reach | Gradient makes hidden foliage readable without recoloring it green |
| Visual, stackable | **Shadow** (`shadow`) | Foliage and globes, with softer foliage attenuation | Attenuate or occlude light beneath the physical masks and retain a legible silhouette | Core attenuation and edge softness | Rain on Glass creates dry plant-shaped foreground shadows |
| Visual, stackable | **Refract** (`refract`) | Distance/normal fields around both layers | Bend, delay, or phase-shift continuous fields around plant geometry without making it solid | Distortion distance and phase displacement | Wave fronts split around globes and reconnect downstream |
| Field, exclusive | **Attractor** (`attractor`) | Boundaries of both layers | Apply a soft distance-field force toward plant edges without forcing entities into hidden cores | Steering or growth bias | Sparkles gather around globe rims and leaf boundaries |
| Field, exclusive | **Repulsor** (`repulsor`) | Clearance around both layers | Apply a soft force away from plants while still allowing traversal when motion wins | Steering force and influence radius | Fireflies drift out of dense foliage without hard collisions |
| Field, exclusive | **Slow Zone** (`slow_zone`) | Clearance around both layers | Reduce local velocity, advection, or semantic cadence near plants without changing topology | Minimum local speed and falloff | Fluid bubbles hang and wobble near roots before escaping |
| Surface, exclusive | **Obstacle** (`obstacle`) | Exact foliage/globe union; clearance for planning | Block contact and route/spawn/placement around calibrated geometry | Route penalty and use of the configured clearance; exact cores remain solid whenever enabled | Snake paths and food avoid foliage and globes |
| Surface, exclusive | **Portal** (`portal`) | Seven named globe regions | Teleport an entering entity to the next globe in a stable directed cycle, preserving normalized entry offset and velocity when the simulation supports them | Exit impulse and portal bloom, not teleport probability | A snake enters `top_left` and exits `top_right` without retriggering immediately |
| Surface, exclusive | **Bumper** (`bumper`) | Seven named globe regions | Reflect or deflect contact using the circular globe normal and emit a bounded impact event | Restitution and impact accent | Pinball globes become physical scoring bumpers |
| Surface, exclusive | **Hazard / Lava** (`hazard`) | Exact foliage/globe union with a clearance warning halo | Apply deterministic damage, decay, despawn, or reset semantics on contact | Severity or decay rate; the plugin documents its domain-specific outcome | Conway cells burn out on contact while Snake loses a life |
| Surface, exclusive | **Habitat** (`habitat`) | Foliage | Bias valid spawning, survival, growth, or shelter toward foliage without making hidden pixels mandatory destinations | Spawn/survival bias | Conway nurseries and synchronized fireflies cluster around foliage edges |
| Lifecycle, stackable | **Emitter** (`emitter`) | Boundaries of both layers | Emit plugin-native particles, waves, seeds, or pulses at a bounded rate | Emission rate and initial energy | Sparkles and reaction-diffusion seeds originate along plant contours |

Portal ordering follows the stable globe region IDs in calibration order:
`top_left`, `top_right`, `upper_middle`, `middle_left`, `middle_right`,
`lower_left`, `lower_right`, then back to `top_left`. A per-entity cooldown lasts
until the entity leaves the exit region plus at least one semantic update, so a
portal can never create an immediate teleport loop.

### Deterministic composition

Every supporting plugin applies active modifiers in the same semantic order,
even though the domain-specific implementation differs:

1. Apply the selected field influence to planned motion, growth, or advection.
2. Integrate movement with the plugin's normal bounded time step.
3. Resolve the selected surface role: obstacle collision, portal transfer,
   bumper reflection, hazard contact, or habitat survival/spawn bias.
4. Schedule bounded emitter events without recursively advancing the simulation.
5. Render the ordinary scene, then apply Refract and Shadow composition.
6. Apply Illuminate last. When Shadow is also active, Illuminate is restricted
   to the semantic boundary/halo so the intended dark core remains visible.

Presentation-only modifiers must not change logical state. Changes to modifier
state invalidate relevant plans and cached frames, but they do not consume
random numbers or advance a simulation tick by themselves.

### Compatibility migration

- Persisted `plant_aware: true` migrates once to active `illuminate` at `0.5`
  and `obstacle` at `1.0`, which captures the dominant intent of the current
  installation behavior.
- Persisted `plant_aware: false` migrates to an empty active set. When the old
  field is absent, use the existing deployment default before applying this
  mapping; direct/headless construction still defaults to empty.
- During the compatibility window, the old API may translate booleans through
  the same mapping, but status and newly saved state use only
  `PlantModifierState`. Do not maintain a vague long-lived `legacy` modifier.
- Curated presets should eventually replace `plant_aware` with modifier
  recommendations. Runtime migration must not rewrite user-authored preset files
  in place.

## First plant-modifier sprint

The first sprint establishes the shared contract and proves all twelve
modifiers across six deliberately different existing plugins. It does not
attempt a shallow mechanical conversion of every shipped animation.

### Showcase matrix

| Plugin | Supported modifiers in sprint one | What it proves |
| --- | --- | --- |
| **Gradient** | Illuminate, Shadow, Refract | Dense, presentation-only composition; visible strengths; unchanged cached static frames |
| **Sparkle** | Illuminate, Attractor, Repulsor, Habitat, Emitter | Sparse particles, distance-field steering, biased spawning, and bounded source events |
| **Snake** | Obstacle, Portal, Hazard | Grid routing, deterministic seven-globe topology, contact semantics, and loop-safe teleportation |
| **Pinball** | Bumper, Portal, Hazard | Continuous collision normals, restitution, scoring/impact events, and contact-role switching |
| **Conway's Life** | Obstacle, Habitat, Hazard, Emitter | Cellular neighborhoods, survival rules, nurseries, burns, and deterministic seed injection |
| **Fluid Tank** | Obstacle, Refract, Slow Zone | Continuous fluids/particles, hard geometry, local velocity scaling, and mask-aware presentation |

### Sprint sequence

1. **Framework:** introduce validated global state, persistence migration,
   manager authority, shared helpers, support declarations, cached derivative
   geometry, API/status fields, preview propagation, and grouped dashboard
   controls. UI controls should show the active animation's support and prevent
   conflicting field or surface selections.
2. **Showcases:** implement one modifier group at a time across the matrix,
   stabilizing shared contracts before duplicating behavior. Each plugin keeps
   semantic state separate from modifier presentation and exposes useful runtime
   counts such as contacts, teleports, emitted entities, and mask errors.
3. **Acceptance:** run deterministic tests, long-enough behavioral simulations,
   real-aspect representative renders/contact sheets, and default/stress
   benchmarks. Tune normalized strengths from visible wall output rather than
   desktop preview alone.
4. **Follow-on migration:** classify the remaining 23 plugins by modifier
   capability and migrate them in compatibility-driven waves. An unsupported
   modifier remains an explicit no-op; no generic post-process is used to claim
   semantic support.

### Sprint acceptance gates

- Empty active state produces byte-identical seeded frames, identical RNG
  consumption, and identical semantic state to an animation built without plant
  integration.
- The manager rejects duplicate IDs, unknown IDs, out-of-range/non-finite
  strengths, multiple field modifiers, and multiple surface roles.
- Compatible modifier combinations have stable frame and state fingerprints,
  and live changes affect the current animation, all future starts, and previews
  without advancing time.
- Global state survives saved-state and deployment round trips. Preset
  recommendations never silently override it.
- Portal tests traverse all seven globe IDs in the documented order, preserve
  entry offset and velocity where meaningful, and prove cooldown recovery.
- Missing/malformed masks produce deterministic empty-geometry behavior and an
  observable error, with no unbounded search or partially updated simulation.
- Each showcase includes a constructed unit scenario and a longer seeded run
  that measures its emergent outcome, not only frame shape.
- Every modifier and representative compatible combination produces a visibly
  distinct rendered frame at 32×138. A labeled contact sheet covers the six
  showcase plugins at the wall's true aspect ratio.
- Default and maximum-strength/stress paths pass the repository's desktop
  benchmark at less than 4 ms plugin p95 on 32×138. Record mean, p95, p99, and
  maximum timings separately; desktop results do not claim Raspberry Pi timing.
- Physical acceptance confirms that strengths are perceptibly different,
  foliage and globes remain legible as distinct geometry, and no default creates
  full-wall flashes or uncomfortable brightness.

## Best first builds

The per-idea notes below name the strongest modifier directions rather than a
required bundle. Modifiers from the same exclusive group are alternate preset
concepts; compatible visual, field, surface, and lifecycle choices may stack
under the composition rules above.

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
- **Useful controls:** drizzle/storm intensity, glass persistence, background scene, wind, refraction strength, and optional Shadow/Refract plant modifiers.
- **Plant modifier opportunities:** **Shadow** leaves dry, dark foliage silhouettes in the wet glass while **Refract** bends city light and droplet trails around both mask layers. As an alternate surface role, **Obstacle** makes globes split streams like raised glass rivets, with **Emitter** adding bounded beads along their upper rims.

### 2. Aurora Curtains

Several translucent curtains fold independently from bottom to top, with slow traveling ripples, faint stars behind them, and occasional brighter knots flowing along a curtain. This should look volumetric: overlapping sheets add light while dark gaps retain depth.

- **Python leverage:** layered divergence-free flow fields, octave noise, phase-advection, and additive palette compositing.
- **Ambient behavior:** very slow global drift, muted boreal colors, bounded peak luminance, and minute-scale changes in activity.
- **Useful controls:** curtain count, solar activity, vertical reach, shimmer, star density, wind direction, and palettes such as boreal, violet, ember, and moon-white.
- **Plant modifier opportunities:** **Refract** folds curtain phase around foliage and globe normals so sheets part and reconnect with real depth; **Shadow** places the plants in the foreground, and **Illuminate** catches their rims in the active aurora palette. **Emitter** can release rare bright knots from globe boundaries without turning the whole mask into a light source.

### 3. Cloud Canyon

Soft banks of fog rise through the tall wall while moonlight or sunset light leaks between them. Dense clouds should occlude light rather than merely tint it; thin edges catch silver or peach highlights and reveal slow internal billowing.

- **Python leverage:** advected multi-octave density fields, cheap Beer-Lambert-style light attenuation, and cached low-resolution simulation upsampling.
- **Ambient behavior:** broad diffuse fill with almost no sharp motion; a full transition from clear to overcast can take 10–20 minutes.
- **Useful controls:** cloud cover, light elevation, wind layers, contrast, color temperature, and day/night mood.
- **Plant modifier opportunities:** **Shadow** gives foliage soft volumetric occlusion and globes denser silhouettes, while **Refract** bends moon shafts around their boundaries. **Slow Zone** creates persistent fog eddies in the clearance field, and **Emitter** can seed wisps from leaf edges after the broader cloud layer has passed.

### 4. Waterfall Veil

Fine streams descend at different speeds, collide with ledges, fragment into mist, and collect briefly in luminous pools before spilling again. The scene can imply a cliff with only a few pixels of rock and use the entire height for falling motion.

- **Python leverage:** height-field terrain, particles constrained by that field, water flux, foam lifetime, mist advection, and simple pool conservation.
- **Ambient behavior:** mostly dark slate and forest tones with cool moving highlights; no uniform bright water column.
- **Useful controls:** flow rate, ledge complexity, mist, pool glow, moon/sunset lighting, and optional Shadow/Obstacle plant modifiers.
- **Plant modifier opportunities:** **Obstacle** turns foliage into porous ledges and globes into boulders that split flow, pool water above them, and create deterministic spill points. **Slow Zone** makes sheltered trickles cling to the clearance field; **Emitter** adds foam and mist at impact edges, while **Portal** offers a surreal preset where water entering one globe pours from the next.

### 5. Tidal Bioluminescence

A dark ocean occupies the lower wall. Slow swells disturb microscopic plankton, producing cyan points and curling luminous wakes that fade over several seconds. Rare deep silhouettes can pass through and trigger wider blooms without turning the scene into a literal aquarium.

- **Python leverage:** a one-dimensional wave surface coupled to a two-dimensional concentration/advection field and sparse wake emitters.
- **Ambient behavior:** near-black navy base, gentle wave rhythm, small light budget, and rare rather than constant spectacles.
- **Useful controls:** tide strength, plankton density, wake frequency, persistence, depth haze, and cyan/green/violet palettes.
- **Plant modifier opportunities:** **Refract** splits wave phase around mask normals, and **Slow Zone** forms calm tide pools in plant clearance. **Habitat** concentrates plankton near foliage, while **Emitter** produces brief bioluminescent blooms along contours whenever a swell crosses them; **Illuminate** then exposes only the freshest disturbed edges.

### 6. Wind in the Reeds

Procedural stems grow up from the bottom and bend in coherent gusts. With Shadow, Habitat, or Slow Zone enabled, calibrated real foliage becomes the foreground canopy: light pools behind it, gusts appear to pass through it, and small motes shelter in its lee.

- **Python leverage:** inverse-kinematic stem chains, a spatial wind field with gust fronts, and mask-derived occlusion/distance fields.
- **Ambient behavior:** warm dusk or cool moonlight, synchronized but imperfect swaying, and long lulls between gusts.
- **Useful controls:** wind, gustiness, stem density, season, motes, silhouette strength, and palette.
- **Plant modifier opportunities:** **Shadow** makes the physical foliage the dominant foreground canopy; **Habitat** shelters motes and virtual stems near its edges, and **Slow Zone** creates a measurable wind lee behind it. **Emitter** releases pollen or fireflies from foliage contours after gust events, while **Illuminate** supplies a restrained dusk rim rather than filling hidden cores.

### 7. Moonlit Fog Banks

Layered fog drifts upward through a forest or mountain silhouette while a hidden moon moves behind it. The moon is rarely shown directly; it reveals itself through halos, soft shafts, and silver edges.

- **Python leverage:** signed-distance silhouettes, low-resolution semi-Lagrangian advection, depth-layer compositing, and mask-aware light scattering.
- **Ambient behavior:** extremely slow, low-saturation motion with a stable dark floor and optional warm pre-dawn transition.
- **Useful controls:** fog depth, moon phase, tree/mountain geometry, drift direction, halo size, and contrast.
- **Plant modifier opportunities:** **Shadow** merges real foliage into the forest silhouette with softer attenuation than the globes, and **Refract** bends moon halos and shafts around both layers. **Slow Zone** holds fog in the plant lee; **Emitter** lets rare wisps or fireflies peel from foliage edges when the hidden moon brightens.

### 8. Desert Wind

Layered dunes evolve through subtle erosion while thin ribbons of sand skim their crests. Light moves gradually from violet predawn through amber sunset, allowing the same geometry to feel different across a long session.

- **Python leverage:** one-dimensional dune height evolution, particle saltation, slope relaxation, and procedural lighting from surface normals.
- **Ambient behavior:** large calm areas of warm light, very sparse moving grains, and hour-scale palette transitions.
- **Useful controls:** wind, grain activity, sun angle, dune scale, haze, and palettes including ochre, Mars, moonlit, and rose.
- **Plant modifier opportunities:** **Obstacle** lets sand accumulate on the windward side of globes and route around foliage as persistent terrain, while **Slow Zone** settles grains into long leeward tails. **Emitter** lifts sparse saltation streams from exposed mask edges, and **Illuminate** catches those ridges only when the simulated sun reaches the correct angle.

## Emergent living systems

### 9. Reaction-Diffusion Garden

Gray-Scott reaction-diffusion chemistry slowly grows spots, stripes, coral branches, fingerprints, and cell-like islands. Older regions can shift palette or become substrate for a second reagent, creating a garden with history rather than an endlessly scrolling texture.

- **Python leverage:** vectorized Laplacian updates, multiple feed/kill regimes, seeded disturbances, and age/composition fields.
- **Ambient behavior:** simulation at roughly 8–15 Hz, slow pattern formation, dark substrate, and luminous edges rather than solid bright blobs.
- **Useful controls:** morphology, growth rate, seeding mode, edge glow, color-by-age, perturbation interval, and plant-mask interaction.
- **Plant modifier opportunities:** **Habitat** changes feed/kill coefficients near foliage so patterns colonize it without requiring masked cells to remain alive; **Obstacle** creates no-flux boundaries, and **Hazard** becomes a deterministic kill field that burns clean cavities. **Emitter** injects bounded reagent seeds along plant contours, while **Illuminate** reveals only active reaction fronts.

### 10. Physarum Network

Thousands of tiny agents explore a nutrient field, reinforce useful routes, abandon inefficient ones, and form a glowing transport network. Nutrient sites can migrate slowly so the network continuously adapts without needing a hard reset.

- **Python leverage:** agent sensing/steering, pheromone deposition and diffusion, resource consumption, pruning, and bounded spatial updates.
- **Ambient behavior:** most pixels remain dark; a small number of thick routes pulse slowly as virtual material moves through them.
- **Useful controls:** agent count, branching, diffusion, nutrient layout, pulse visibility, palette, and whether plant-covered areas attract or repel growth.
- **Plant modifier opportunities:** **Attractor** and **Repulsor** provide explicit alternatives to the current vague mask preference, while **Obstacle** forces the network to discover efficient routes around real geometry. **Habitat** makes foliage a persistent nutrient source; **Portal** links globe regions into nonlocal shortcuts, and **Emitter** launches bounded explorer cohorts from plant boundaries after route collapse.

### 11. Murmuration

A flock of points compresses into ribbons, sheets, and vortices as invisible pressure waves move through it. Occasionally the flock parts around an obstacle or follows a soft light source, then settles back into a quiet formation.

- **Python leverage:** boids with a spatial hash, curl-noise steering, obstacle avoidance, projection trails, and depth-based brightness.
- **Ambient behavior:** restrained density and short, soft trails; dramatic shape changes happen infrequently and never flash the entire wall.
- **Useful controls:** flock size, cohesion, turbulence, trail length, altitude bias, predator events, and dusk/pearl/neon palettes.
- **Plant modifier opportunities:** **Repulsor** makes the flock part smoothly around foliage before contact, while **Obstacle** produces sharper last-moment avoidance around solid geometry. **Attractor** turns foliage edges into temporary roosts, **Slow Zone** compresses the flock into sheltered knots, and **Portal** can send a ribbon entering one globe out through the next without breaking velocity coherence.

### 12. Firefly Synchrony

Individual fireflies wander around a dark meadow and adjust their internal clocks when nearby neighbors flash. Local clusters gradually synchronize, compete with other rhythms, fall apart in wind, and assemble again.

- **Python leverage:** Kuramoto-style coupled oscillators, neighbor lookup, agent motion, local wind, and energy/recovery cycles.
- **Ambient behavior:** soft sub-second pulses distributed across the wall, with a hard cap on the fraction allowed to peak together.
- **Useful controls:** population, coupling radius, synchrony, wandering, pulse softness, meadow glow, and plant-mask habitat preference.
- **Plant modifier opportunities:** **Habitat** biases resting, spawning, and oscillator recovery toward foliage; **Attractor** gathers active fireflies at its boundary, whereas **Repulsor** preserves dark plant silhouettes. **Emitter** releases small cohorts from different contour segments, **Slow Zone** creates calm synchronization pockets, and **Illuminate** lets collective flashes briefly trace the physical plants.

### 13. Cyclic Reef

A multi-state cyclic cellular automaton creates waves of competing color that resemble coral polyps, microscopic organisms, or animated mineral bands. Small grazing agents can open dark channels that the reef slowly recolonizes.

- **Python leverage:** vectorized neighborhood transitions, multiple species rules, local mutation, age fields, and optional mobile disruptors.
- **Ambient behavior:** low update rate, limited palette, gentle crossfades between states, and broad dark cavities for contrast.
- **Useful controls:** state count, threshold, mutation, grazer density, edge glow, palette, and topology.
- **Plant modifier opportunities:** **Obstacle** removes masked cells from neighborhood exchange to form permanent reef cavities; **Habitat** lowers transition thresholds near foliage, while **Hazard** creates bleaching fronts. **Emitter** seeds new state waves on semantic edges, and **Portal** makes matching globe cells nonlocal neighbors so color cycles jump through the seven-region topology.

### 14. Frostwork

Ice crystals grow from the borders through diffusion-limited aggregation, branch into empty space, catch blue-white light, then sublimate and regrow from a new cold front. Growth history determines color and sparkle intensity.

- **Python leverage:** random walkers accelerated by occupancy distance/bounds, branch aging, heat diffusion, and controlled melt fronts.
- **Ambient behavior:** growth is slow enough to watch over minutes; sparkle is localized to newly formed tips and never random full-field glitter.
- **Useful controls:** temperature, branching bias, growth speed, melt cycle, crystal color, and border/seed placement.
- **Plant modifier opportunities:** **Emitter** nucleates crystals from foliage and globe boundaries, and **Attractor** biases walkers toward those cold contours without guaranteeing attachment. **Obstacle** forces branches to split around the masks; **Hazard** treats them as warm melt zones, while **Illuminate** catches only newly grown tips and globe rims.

### 15. Mycelial Pulse

A subterranean network grows between dim resource nodes, branches, fuses with itself, reroutes after damage, and sends visible nutrient pulses along mature paths. Fruiting bodies can appear rarely near the top of the network and fade after a short season.

- **Python leverage:** graph growth guided by a scalar field, path reinforcement, loop formation, resource transport, and graph-distance pulse propagation.
- **Ambient behavior:** slow amber or blue-green veins on a nearly black ground, with rare brighter transfers instead of constant motion.
- **Useful controls:** resource density, branching, transport rate, season length, fruiting frequency, and woodland/alien palettes.
- **Plant modifier opportunities:** **Habitat** makes foliage a durable resource bed and fruiting site; **Obstacle** forces hyphae to route around solid cores, and **Hazard** sterilizes contacted branches so the graph must heal. **Portal** joins globe regions with long-distance graph edges, while **Emitter** begins new growth tips or nutrient pulses at active plant boundaries.

### 16. Lichen Colonies

Several differently colored colonies spread over a virtual stone surface. They compete at boundaries, retreat during dry periods, bloom after moisture passes, and leave mineral stains that influence future growth.

- **Python leverage:** competing growth fronts, moisture and nutrient diffusion, dormancy, and persistent substrate memory.
- **Ambient behavior:** extremely slow evolution with muted mineral colors; meaningful changes occur over minutes rather than frames.
- **Useful controls:** colony count, climate cycle, competition, surface roughness, edge illumination, and palette.
- **Plant modifier opportunities:** **Habitat** supplies persistent moisture near foliage and changes which colonies can survive there; **Obstacle** leaves bare mineral silhouettes, while **Hazard** creates dry sterilized patches with visible recolonization fronts. **Emitter** releases spores from mature contour segments, and **Illuminate** highlights only wet or actively growing edges.

### 17. Rootlight

Roots descend and fork from plant-mask regions, seeking drifting pockets of water and minerals. Successful routes thicken and carry warm pulses back upward; unused branches slowly recede. With the real plants above them, the wall becomes an imaginary cross-section of the same habitat.

- **Python leverage:** tropism-driven branching, resource fields, collision and anastomosis, transport along a dynamic tree/graph, and mask-derived root origins.
- **Ambient behavior:** dark soil, sparse golden or cyan pulses, and growth measured in minutes.
- **Useful controls:** root density, branching, resource renewal, gravity bias, pulse rate, soil palette, and plant-mask coupling.
- **Plant modifier opportunities:** **Emitter** is the natural root-origin control, launching bounded branches from foliage contours; **Habitat** reinforces roots that remain near the real plant layer. **Attractor** makes the seven globes water/mineral reservoirs, **Obstacle** makes roots wrap around their shells, and **Portal** can turn globe pairs in the directed cycle into grafts that carry nutrient pulses across the wall.

### 18. Particle Life

Several particle species attract and repel one another at different distances. They spontaneously form cells, chains, orbiting colonies, migrating blobs, and dissolving membranes without any authored choreography.

- **Python leverage:** species interaction matrices, grid-based neighbor acceleration, stable integration, cluster metrics, and periodic bounded perturbations.
- **Ambient behavior:** low particle count, soft splats and trails, slow forces, and palettes that prevent every species from competing at full brightness.
- **Useful controls:** species count, interaction family, population, viscosity, trail decay, mutation interval, and palette.
- **Plant modifier opportunities:** **Attractor**, **Repulsor**, and **Slow Zone** become external fields layered over the species interaction matrix without mutating it. **Obstacle** produces membrane-like collisions, **Portal** preserves particle velocity across globe transfers, **Hazard** selectively decays contacting particles, and **Habitat** or **Emitter** creates bounded reproduction zones along foliage.

## Mathematical light sculptures

### 19. Flow-Field Silk

Fine luminous threads are released into a smooth vector field and weave around one another as the field changes. Threads can fray, braid, vanish into shadow, and be replaced upstream, producing the feel of silk in water rather than a generic noise texture.

- **Python leverage:** curl-noise or analytic vector fields, streamline integration, trail accumulation, and anti-aliased line rasterization.
- **Ambient behavior:** a small number of slow threads over a broad colored shadow field; motion remains coherent and directional.
- **Useful controls:** thread count, field family, turbulence, persistence, width, direction, and palette.
- **Plant modifier opportunities:** **Refract** bends the underlying vector field around mask normals so threads wrap the physical plants; **Attractor**, **Repulsor**, or **Slow Zone** produces gathering, parting, or suspended drapery. **Obstacle** cuts and respawns colliding threads, **Portal** stitches one globe to the next, and **Emitter** releases replacement fibers from selected contour segments.

### 20. Strange Attractor Observatory

Lorenz, Rössler, Clifford, and custom attractors are projected into the wall as slowly rotating constellations. Dense regions glow like nebulae while a bright tracer reveals the live trajectory.

- **Python leverage:** numerical integration, projection/camera transforms, density accumulation, histogram tone mapping, and attractor morphing.
- **Ambient behavior:** dim accumulated clouds with one restrained tracer; camera movement takes minutes rather than seconds.
- **Useful controls:** attractor, integration speed, viewpoint drift, decay, tracer visibility, symmetry, and palette.
- **Plant modifier opportunities:** **Refract** warps only the projection and density field around plant geometry, preserving the integrated attractor trajectory for deterministic comparison. **Attractor** or **Repulsor** can instead alter the live tracer in screen space, **Portal** creates discontinuous globe-to-globe observations, and **Emitter** seeds restrained secondary tracers from plant boundaries.

### 21. Lava-Lamp Metaballs

Buoyant blobs heat near the bottom, rise, merge, cool, and sink. Their implicit surfaces blend smoothly, with a warm inner core and soft halo that turns the wall into a tall, modern lava lamp.

- **Python leverage:** particle buoyancy, temperature exchange, metaball scalar fields, contour bands, and volume-conserving split/merge events.
- **Ambient behavior:** very slow motion, a dark background, and large rounded regions that cast useful colored light without visual noise.
- **Useful controls:** blob count, viscosity, heat, merge tendency, halo, glass tint, and classic/ice/forest palettes.
- **Plant modifier opportunities:** **Obstacle** makes masks solid inclusions that split and deform rising blobs while preserving volume; **Slow Zone** cools and thickens material near plant clearance. **Portal** transfers a conserved portion of a blob between globe vessels, **Hazard** makes plant cores superheated split zones, and **Shadow** keeps the real foliage legible against the luminous fluid.

### 22. Living Stained Glass

Large Voronoi cells drift almost imperceptibly while their shared edges relax toward a balanced tessellation. Light wanders between panes as if clouds were passing outside; occasional cells divide or merge and the lead network heals around them.

- **Python leverage:** Voronoi or nearest-seed fields, Lloyd relaxation, adjacency graphs, cell lifecycle, and procedural transmitted light.
- **Ambient behavior:** broad stable panes make excellent room illumination, while structural changes are rare and gentle.
- **Useful controls:** pane count, geometry drift, lead width, light direction, color harmony, division rate, and cathedral/sea/amber palettes.
- **Plant modifier opportunities:** **Shadow** incorporates foliage as organic leadwork and globes as dark medallions; **Illuminate** turns their boundaries into transmitted-light rims. **Refract** bends pane light without changing the tessellation, while **Obstacle** pins Voronoi boundaries around masks so the glass geometry slowly heals into the installation rather than painting over it.

### 23. Chladni Sand

Virtual grains migrate toward the nodal lines of a vibrating plate. As frequencies slowly crossfade, crisp geometric figures loosen into clouds and reorganize into new symmetries.

- **Python leverage:** modal wave equations, gradients of vibration energy, particle relaxation, and interpolation between compatible modes.
- **Ambient behavior:** modes hold for minutes, transitions are gradual, and only the settled sand and a faint plate glow are illuminated.
- **Useful controls:** mode family, transition time, grain count, damping, plate glow, symmetry, and sand/light palette.
- **Plant modifier opportunities:** **Obstacle** excludes grains from exact masks and creates crisp accumulation ridges; **Attractor** or **Repulsor** shifts the energy gradient toward or away from plant edges, and **Slow Zone** adds local damping. **Emitter** introduces measured grains along contours, while **Portal** makes globe regions coupled apertures that transfer grains without changing total mass.

### 24. Pendulum Wave

Rows of virtual pendulums with carefully related periods drift in and out of phase, forming traveling waves, braids, checkerboards, and moments of total alignment. Trails turn the motion into a spatial light sculpture.

- **Python leverage:** exact oscillator phase relationships, perspective projection, trail persistence, and automatic selection of visually interesting recurrence windows.
- **Ambient behavior:** predictable, slow, and hypnotic; alignment events brighten locally rather than flashing the whole grid.
- **Useful controls:** oscillator count, recurrence period, trail, perspective, bob size, and palette.
- **Plant modifier opportunities:** **Refract** bends trails around plant normals while leaving oscillator phases mathematically exact; **Shadow** places foliage in front of the sculpture. **Slow Zone** is the semantic option, introducing a local phase lag only while a bob crosses plant clearance, and **Emitter** can launch a bounded secondary ripple when a bob crosses a globe boundary.

### 25. Moiré Loom

Two or three translucent line lattices rotate, bend, and breathe at slightly different rates. Their interference creates large traveling shapes much more complex than any input layer, like woven light viewed through moving fabric.

- **Python leverage:** analytic line-distance fields, nonlinear warps, phase locking, and gamma-correct layer compositing.
- **Ambient behavior:** low contrast and slow sub-degree motion prevent shimmer from becoming harsh; the macro pattern does most of the work.
- **Useful controls:** layer count, weave geometry, spacing, warp, rotation rates, edge softness, and palette.
- **Plant modifier opportunities:** **Refract** turns foliage and each globe into stable phase lenses, producing large interference contours rather than noisy per-pixel distortion. **Shadow** creates woven negative-space silhouettes, **Illuminate** traces only constructive interference on their rims, and **Slow Zone** lets lattice phase drift lag near plants before smoothly rejoining the global weave.

### 26. Magnetic Flux

Drifting magnetic poles generate curved field lines. Charged sparks spiral briefly along those lines, poles split or annihilate, and iron-filament textures turn to follow the changing field.

- **Python leverage:** vector field evaluation, streamline tracing, pole dynamics, charged-particle integration, and line-density rendering.
- **Ambient behavior:** mostly dark field lines with brief low-energy travelers; pole changes happen slowly and preserve visual continuity.
- **Useful controls:** pole count, field strength, tracer rate, line density, drift, polarity colors, and glow.
- **Plant modifier opportunities:** **Attractor** and **Repulsor** map plant boundaries to soft magnetic poles, while **Obstacle** bends streamlines around impenetrable material. **Bumper** reflects charged sparks from circular globe normals, **Portal** joins globe field lines into wormholes, and **Emitter** releases bounded tracers whose polarity follows the local field.

### 27. Quasicrystal Bloom

Several plane waves interfere to create non-repeating five-, eight-, ten-, or twelve-fold symmetries. The phase relationships drift slowly, making rosettes open, tunnel inward, and rearrange without translating like an ordinary wave.

- **Python leverage:** vectorized wave superposition, symmetry sets, nonlinear palette mapping, phase modulation, and optional domain warping.
- **Ambient behavior:** broad low-frequency structures, slow phase drift, and palette bands with restrained contrast.
- **Useful controls:** symmetry order, spatial scale, phase speed, warp, band softness, center drift, and palette.
- **Plant modifier opportunities:** **Refract** adds mask-derived phase offsets to the plane waves, making rosettes fold around real geometry without breaking global symmetry elsewhere. **Shadow** reserves strong negative space, **Illuminate** selects constructive rim bands, **Slow Zone** retards local phase drift, and **Emitter** introduces temporary low-amplitude wave centers at plant contours.

### 28. Cellular Tapestry

A one-dimensional cellular automaton writes one new row at a time, so the wall's height becomes a moving historical record. Multiple rules can crossfade or exchange boundary conditions, producing lace, triangles, woven bands, and surprising pseudo-random structures.

- **Python leverage:** bitwise rule evaluation, mutation events, rule scheduling, age-based coloring, and deterministic seeded replay.
- **Ambient behavior:** only one row is born at a time; the tapestry scrolls slowly and old rows fade into a dark textile background.
- **Useful controls:** rule or rule playlist, row interval, mutation, wrap, color-by-state/age, fade length, and palette.
- **Plant modifier opportunities:** As each new row crosses calibrated geometry, **Obstacle** forces masked bits dead, **Habitat** biases births at foliage columns, and **Hazard** clears short-lived holes whose history then scrolls down the wall. **Emitter** injects deterministic edge seeds, while **Portal** copies state entering one globe's columns into the next globe region to create nonlocal woven motifs.

## Long-form scenes and responsive installations

### 29. Circadian Window

A procedural sky tracks local time from deep night through dawn, daylight, sunset, and twilight. Stars rotate, clouds inherit the current wind, the moon follows a plausible arc, and artificial window light appears after dark.

- **Python leverage:** solar/lunar position approximations, continuous color-temperature curves, star coordinates, cloud simulation, and isolated time providers for testing.
- **Ambient behavior:** its primary purpose is pleasant all-day room light; changes are nearly imperceptible moment to moment.
- **Useful controls:** latitude/longitude or simple offset, time scale, cloud cover, horizon style, stars, interior warmth, and brightness schedule.
- **Plant modifier opportunities:** **Shadow** makes the physical foliage a convincing foreground silhouette that changes character from dawn to moonlight; **Illuminate** provides restrained backlit rims at solar golden hour and lunar rise. **Refract** bends cloud and halo light around globes, while **Emitter** releases stars, pollen, or fireflies from plant contours only during appropriate dayparts.

### 30. City at Dusk

A vertical city assembles itself from procedural towers, windows, rooftop gardens, signs, and elevated bridges. Rooms turn on and off according to small occupancy models, elevators travel between floors, and the sky completes a slow day/night cycle.

- **Python leverage:** shape grammar generation, building adjacency, per-room schedules, tiny agent stories, parallax haze, and deterministic city seeds.
- **Ambient behavior:** windows are warm isolated pools in a large blue-black scene; signs are subdued and traffic is sparse.
- **Useful controls:** architecture style, city density, time of day, occupancy, weather, sign activity, and window palette.
- **Plant modifier opportunities:** **Shadow** turns foliage into rooftop gardens and foreground trees, while **Illuminate** selectively maps warm window light onto their edges. **Obstacle** makes roads, bridges, elevators, and sky traffic route around plant geometry; **Habitat** clusters occupied garden rooms near foliage, **Portal** makes globes transit hubs, and **Emitter** launches sparse traffic or window activity from those hubs.

### 31. Night Train Windows

The wall becomes a passing landscape seen through a train: distant mountains move slowly, trees and utility poles cross faster, window reflections hover in front, and small towns occasionally appear and disappear.

- **Python leverage:** seeded procedural terrain, multi-layer parallax, object spawning from a route grammar, weather, and reflection compositing.
- **Ambient behavior:** long stretches of quiet darkness punctuated by warm towns; speed can be slow enough for sleep-friendly use.
- **Useful controls:** route type, train speed, weather, moon, town frequency, reflection strength, and night/sunrise palettes.
- **Plant modifier opportunities:** **Shadow** treats foliage as near-window reflections that remain fixed while landscape layers slide behind them; **Refract** bends rain streaks and distant town lights around both masks. **Slow Zone** lets condensation linger near plants, **Illuminate** catches passing station light on their rims, and **Emitter** adds rare fireflies or sparks that peel away into the moving landscape.

### 32. Terrarium Cross-Section

Above ground, leaves open and close through a day cycle while condensation forms on glass. Below ground, moisture moves through soil, roots grow, fungi connect them, and tiny burrowers reshape a few passages.

- **Python leverage:** coupled but low-rate moisture, root, fungal, and agent simulations with a day/night driver and persistent world state.
- **Ambient behavior:** the scene is mostly earthy darkness and green-gold pools; subterranean events are subtle discoveries rather than constant action.
- **Useful controls:** humidity, season, plant density, underground visibility, creature activity, lifecycle length, and realistic/fantasy palettes.
- **Plant modifier opportunities:** **Habitat** is the native mode: real foliage anchors canopy growth, moisture retention, nesting, and fungal exchange. **Obstacle** makes globe shells and dense roots physical terrain, **Slow Zone** represents damp shelter, **Hazard** supplies drought or rot zones, **Portal** links globe reservoirs through subterranean passages, and **Emitter** starts roots, spores, condensation, or tiny creatures from semantic boundaries.

### 33. Deep-Space Survey

A virtual telescope slowly scans a procedurally generated sky. Star fields reveal nebular dust, gravitational-lens arcs, variable stars, and rare comets; exposures accumulate detail and then slew gently to a new target.

- **Python leverage:** seeded star catalogs, point-spread functions, layered fractal dust, exposure accumulation, orbital paths, and camera scheduling.
- **Ambient behavior:** sparse pinpoints over a deep colored black with very slow camera motion and no rapid hyperspace streaks.
- **Useful controls:** target family, exposure time, star density, telescope drift, labels off/on, palette, and event rarity.
- **Plant modifier opportunities:** **Shadow** turns foliage into dark molecular clouds and globes into occulting bodies; **Illuminate** reveals ionized rims as exposure accumulates. **Refract** uses globe normals as gravitational lenses, **Portal** makes them stable wormholes between survey fields, and **Emitter** schedules comets, pulsars, or star-birth events from plant contours without changing the seeded catalog.

### 34. Lantern Weather

Paper lanterns float at different depths and respond to a simulated breeze. Their flames breathe independently, lanterns shelter one another from gusts, and the sky treatment reflects current or fictional weather.

- **Python leverage:** simple rigid-body sway, thermal lift, flock-like spacing, wind fields, paper-light transmission, and optional weather input.
- **Ambient behavior:** warm pools of light dominate; movement is slow, collisions are soft, and storm mode remains dim rather than flashy.
- **Useful controls:** lantern count, wind, lift, depth, paper colors, weather source, and launch frequency.
- **Plant modifier opportunities:** **Repulsor** gives lanterns soft early avoidance, while **Obstacle** lets them catch, slide, and lift away from foliage or globes after bounded contact. **Slow Zone** creates sheltered air pockets, **Habitat** lets lanterns settle near foliage like roosting lights, **Portal** relaunches one entering a globe from the next, and **Illuminate** makes nearby paper light backlight the real plants.

### 35. Lunar Tide Clock

Instead of displaying digits, the wall shows time through a rising and falling luminous tide, the moon's phase and altitude, and small orbital markers. It works as an abstract clock first and a scientifically inspired ambient piece second.

- **Python leverage:** time and lunar-phase calculations, harmonic tide approximation, wave rendering, and an overridable clock/data adapter.
- **Ambient behavior:** minute-rate semantic updates with smooth wave interpolation and a stable cool nighttime light envelope.
- **Useful controls:** location/offset, real or accelerated time, tide exaggeration, moon size, markers, palette, and optional compact time readout.
- **Plant modifier opportunities:** **Refract** bends tide bands around both mask layers while **Obstacle** makes globes islands and foliage shoreline; **Slow Zone** creates lingering tide pools in clearance. **Emitter** adds bounded foam or bioluminescence at crossings, **Illuminate** exposes moonlit rims, and any compact time readout must relocate into safe space rather than clip behind plants.

### 36. Solar Weather

The wall shows a stylized sun surface with convection cells, prominences, magnetic loops, and occasional ejections. It can run entirely procedurally or gently bias activity from current public solar indices when a data adapter is available.

- **Python leverage:** evolving cellular convection, magnetic-field arcs, edge plasma particles, seeded event scheduling, caching, and optional API normalization.
- **Ambient behavior:** deep ember and burgundy rather than full yellow-white; rare events unfold over seconds with no flash cut.
- **Useful controls:** activity, convection scale, prominence rate, rotation, corona strength, data mode, and ember/ultraviolet palettes.
- **Plant modifier opportunities:** **Refract** bends convection and magnetic-loop presentation around masks; **Shadow** makes plants read as cool sunspots, and **Illuminate** gives them a bounded corona. **Hazard** treats their cores as hotter flare zones, **Portal** connects globe anchors with long magnetic loops, and **Emitter** launches prominences or plasma particles from contour segments under the normal event-rate cap.

### 37. Presence-Responsive Shoal

A calm abstract shoal swims through dark water autonomously. If a local camera, distance sensor, or dashboard pointer is later provided, the fish-like agents gather near slow movement, scatter from abrupt movement, and then settle naturally.

- **Python leverage:** boids, obstacle and silhouette avoidance, spatial indexing, input filtering, and a fully autonomous virtual-observer fallback.
- **Ambient behavior:** silhouettes and soft wake light rather than bright cartoon fish; response is damped to avoid twitchiness.
- **Useful controls:** population, curiosity, startle response, wake strength, depth, input source, and palette.
- **Plant modifier opportunities:** **Habitat** makes foliage safe cover and spawning territory; **Repulsor** produces soft schooling avoidance, while **Obstacle** supplies hard collision geometry. **Slow Zone** creates sheltered currents, **Portal** turns globes into caves that preserve heading on exit, **Emitter** releases bounded fry from foliage edges, and **Illuminate** lets wakes briefly reveal the plants without filling them continuously.

### 38. Audio-Reactive Hush

This is the opposite of a nightclub visualizer. A slow ambient field listens only for broad energy, rhythm, and spectral balance: conversation warms nearby regions, bass gently bends the field, and quiet lets it return to an idle breathing state.

- **Python leverage:** streaming FFT bands, automatic gain control, beat confidence, envelope followers, input health monitoring, and procedural fallback signals.
- **Ambient behavior:** strict rate-of-change and brightness limits; transients cannot create flashes, and the idle mode is complete on its own.
- **Useful controls:** sensitivity, response time, spectral mapping, calmness limit, privacy-preserving local input, fallback mode, and palette.
- **Plant modifier opportunities:** **Illuminate** maps slow broadband energy to restrained plant luminance, while **Shadow** preserves quiet silhouettes and **Refract** lets bass bend the ambient field around them. **Slow Zone** locally lengthens attack/release envelopes, **Emitter** releases bounded motes only on high-confidence events, and **Portal** echoes a pulse through the seven globes in order without increasing total light energy.

## Shared building blocks worth adding

Several of these can share infrastructure without collapsing into one giant plugin:

- A cached logical `(height, width)` coordinate/mapping helper with consistent vertical orientation and optional serpentine conversion.
- Extensions to the shared plant-mask cache for per-layer distance/normal fields, semantic edges, blurred occlusion, and stable named globe-region masks.
- Allocation-free glow, trail-decay, soft-point, anti-aliased line, and gamma-aware additive compositing primitives.
- A seeded 2D value/curl-noise field implemented with NumPy and cached geometry, avoiding a new heavy dependency for every plugin.
- A fixed-step simulation helper with bounded catch-up, source-rate render throttling, and a deterministic reset/reseed policy.
- Small time, weather, audio, and presence adapters whose last-known/autonomous fallback behavior is explicit and testable.
- Palette LUTs with a luminance cap, so visually different presets retain roughly comparable room brightness and power draw.

## Selection test for future implementation

An idea is ready to build when it has a clear answer to all of these:

1. What changes every simulation step, what changes only on an event, and what is presentation-only?
2. What makes the result meaningfully better than a pre-rendered GIF or a moving gradient?
3. How does it use the 32×138 shape, and which explicit plant modifiers does it support without changing the all-off path?
4. What are the quiet default, showcase preset, and low-brightness nighttime preset?
5. How does it remain interesting for at least ten minutes without becoming hectic?
6. What work is bounded at maximum density, and can the default path stay below the repository's 4 ms plugin p95 budget on the desktop benchmark?
7. Which deterministic simulation assertions and representative rendered frames will prove that it behaves and looks as intended?
